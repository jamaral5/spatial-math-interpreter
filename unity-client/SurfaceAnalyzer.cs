using UnityEngine;

/// <summary>
/// Measures what a surface is doing at one point, and classifies it.
///
/// This is the half of the AR Math Interpreter that does the actual mathematics.
/// Everything it produces is sent to the backend as finished results; the language
/// model on the other end is given no problem to solve, only numbers to describe.
/// That is the whole point of the split, and it is why this file has no networking
/// in it and the networking file has no mathematics in it.
///
/// MATH BACKGROUND
/// ───────────────
/// TangentPlaneRenderer already estimates the two FIRST derivatives, fx and fy, which
/// tell you the slope in each direction. To say anything about the SHAPE of a surface
/// you need the second derivatives as well - the rate at which those slopes are
/// themselves changing:
///
///     fxx   how the x-slope changes as you move along x   (bending, front to back)
///     fyy   how the y-slope changes as you move along y   (bending, side to side)
///     fxy   how the x-slope changes as you move along y   (twist)
///
/// Those three are the Hessian, and the single number that summarises them is the
/// discriminant:
///
///     D = fxx * fyy - fxy^2
///
/// The second-derivative test reads D like this:
///
///     D > 0 and fxx > 0   the surface cups upward in every direction   - a bowl
///     D > 0 and fxx < 0   the surface caps downward in every direction - a dome
///     D < 0               it rises one way and falls another           - a saddle
///     D = 0               the test cannot tell you                     - inconclusive
///
/// THE PART THAT IS EASY TO GET WRONG
/// ──────────────────────────────────
/// That test only identifies a maximum or a minimum AT A CRITICAL POINT - a place
/// where the ground is level, meaning both first derivatives are (near enough) zero.
/// Halfway down a hillside a bowl-shaped patch of surface is still a hillside; calling
/// it a "local minimum" would be plainly false.
///
/// So the classification here is deliberately two separate questions:
///
///     is the ground level here?        -> isCriticalPoint, from the gradient
///     which way does it curve?         -> shape, from the discriminant
///
/// and only when the answer to the first is yes does the second become a claim about
/// a maximum or a minimum. Being honest about what the maths does not settle is worth
/// more to a student than a confident label that is wrong.
///
/// All the arithmetic below runs in double even though Evaluate returns float. The
/// second derivatives are built from differences of nearly equal numbers divided by
/// h squared, which is the classic recipe for cancellation error, and the extra
/// precision costs nothing at nine evaluations per click.
/// </summary>
public static class SurfaceAnalyzer
{
    /// <summary>Which way the surface curves, independent of whether it is level.</summary>
    public enum SurfaceShape
    {
        Bowl,           // D > 0, fxx > 0 - cups upward in every direction
        Dome,           // D > 0, fxx < 0 - caps downward in every direction
        Saddle,         // D < 0          - up one way, down another
        TroughOrRidge,  // D ~ 0          - flat in at least one direction; test is blind
        Undefined       // the surface is not defined near this point at all
    }

    /// <summary>
    /// Everything measured at one point. A plain struct rather than a class so that
    /// clicking around a surface does not generate garbage for the collector - which
    /// matters more on a headset than on a desktop.
    /// </summary>
    public struct Analysis
    {
        public bool valid;              // false when the surface is undefined nearby

        public float x, y, f;           // the point, in MATH coordinates (not Unity's)

        public float fx, fy;            // first partials
        public float gradientMagnitude; // sqrt(fx^2 + fy^2) - how steep it is
        public float headingDegrees;    // direction of steepest ascent, from the +x axis
        public bool hasHeading;         // false on level ground, where uphill has no direction

        public float fxx, fyy, fxy;     // second partials
        public float discriminant;      // fxx*fyy - fxy^2

        public bool isCriticalPoint;    // is the ground level here?
        public SurfaceShape shape;      // which way does it curve?
        public string kind;             // the two combined, in words
    }

    /// <summary>
    /// How close the gradient has to be to zero before the point counts as level.
    ///
    /// It cannot be exactly zero: fx and fy are numerical estimates, so even at a true
    /// critical point they come back as small non-zero noise. Too small a tolerance and
    /// the exact top of a hill is never recognised as a maximum; too large and a gentle
    /// but real slope gets called flat. 0.01 sits comfortably between the two for the
    /// step size this project uses.
    /// </summary>
    public const float DefaultCriticalTolerance = 0.01f;

    /// <summary>
    /// How close the discriminant has to be to zero before the test is treated as
    /// inconclusive. Second derivatives are noisier than first ones - they divide by
    /// h squared, which amplifies the error - so this is looser than it looks.
    /// </summary>
    public const float DefaultCurvatureTolerance = 0.001f;

    /// <summary>
    /// Measure the surface at a clicked world point.
    /// </summary>
    /// <param name="graph">The graph that was actually clicked. Passed in rather than
    /// looked up so this stays correct with several equations on screen at once.</param>
    /// <param name="worldPoint">The hit point from the raycast, in Unity world space.</param>
    /// <param name="h">The nudge used for numerical differentiation. Match
    /// TangentPlaneRenderer.h so the panel and the drawn plane never disagree.</param>
    public static Analysis Analyze(
        GraphRenderer graph,
        Vector3 worldPoint,
        float h = 0.05f,
        float criticalTolerance = DefaultCriticalTolerance,
        float curvatureTolerance = DefaultCurvatureTolerance)
    {
        var result = new Analysis();

        if (graph == null || h <= 0f) return result;   // valid stays false

        // Unity's axes are not the maths axes. World X is the input x, world Z is the
        // second input we call y, and world Y is the output height. Getting this
        // backwards is the single easiest mistake to make in this project.
        double x0 = worldPoint.x;
        double y0 = worldPoint.z;

        // The 3x3 stencil. Nine samples around the point, which is the smallest set
        // that yields all three second derivatives by central differences.
        //
        //        f_mp   f_0p   f_pp        (y + h)
        //        f_m0   f_00   f_p0        (y)
        //        f_mm   f_0m   f_pm        (y - h)
        //       (x-h)  (x)    (x+h)
        double f00 = Sample(graph, x0,     y0);
        double fp0 = Sample(graph, x0 + h, y0);
        double fm0 = Sample(graph, x0 - h, y0);
        double f0p = Sample(graph, x0,     y0 + h);
        double f0m = Sample(graph, x0,     y0 - h);
        double fpp = Sample(graph, x0 + h, y0 + h);
        double fpm = Sample(graph, x0 + h, y0 - h);
        double fmp = Sample(graph, x0 - h, y0 + h);
        double fmm = Sample(graph, x0 - h, y0 - h);

        // A single NaN anywhere in the stencil poisons everything downstream, and a
        // NaN travelling as far as the explanation panel would be described to a
        // student as though it were a slope. Catch it here instead.
        if (!IsFinite(f00) || !IsFinite(fp0) || !IsFinite(fm0) ||
            !IsFinite(f0p) || !IsFinite(f0m) || !IsFinite(fpp) ||
            !IsFinite(fpm) || !IsFinite(fmp) || !IsFinite(fmm))
        {
            result.x = (float)x0;
            result.y = (float)y0;
            result.shape = SurfaceShape.Undefined;
            result.kind = "undefined near this point";
            return result;
        }

        // ── First derivatives: rise over run, measured very close in ──────
        double fx = (fp0 - fm0) / (2.0 * h);
        double fy = (f0p - f0m) / (2.0 * h);

        // ── Second derivatives ────────────────────────────────────────────
        // fxx compares the step up on one side with the step down on the other. If
        // they match, the surface is straight along x and fxx is zero; if the far side
        // rises more than the near side falls, it is curving upward.
        double fxx = (fp0 - 2.0 * f00 + fm0) / (h * h);
        double fyy = (f0p - 2.0 * f00 + f0m) / (h * h);

        // The mixed partial needs the four corners: it measures how much the x-slope
        // changes when you shift along y, which is the twist in the surface.
        double fxy = (fpp - fpm - fmp + fmm) / (4.0 * h * h);

        double discriminant = fxx * fyy - fxy * fxy;
        double gradientMagnitude = System.Math.Sqrt(fx * fx + fy * fy);

        result.valid = true;
        result.x = (float)x0;
        result.y = (float)y0;
        result.f = (float)f00;
        result.fx = (float)fx;
        result.fy = (float)fy;
        result.fxx = (float)fxx;
        result.fyy = (float)fyy;
        result.fxy = (float)fxy;
        result.discriminant = (float)discriminant;
        result.gradientMagnitude = (float)gradientMagnitude;

        // ── Question one: is the ground level here? ───────────────────────
        result.isCriticalPoint = gradientMagnitude <= criticalTolerance;

        // The gradient points straight uphill. Its compass bearing is only meaningful
        // when there IS an uphill - on level ground the direction is pure noise, so it
        // is withheld rather than reported as a confident zero degrees.
        if (!result.isCriticalPoint)
        {
            result.hasHeading = true;
            float heading = Mathf.Atan2((float)fy, (float)fx) * Mathf.Rad2Deg;
            result.headingDegrees = heading < 0f ? heading + 360f : heading;
        }

        // ── Question two: which way does it curve? ────────────────────────
        if (discriminant > curvatureTolerance)
        {
            // D > 0 forces fxx and fyy to share a sign and both be non-zero, so
            // testing fxx alone is safe here.
            result.shape = fxx > 0.0 ? SurfaceShape.Bowl : SurfaceShape.Dome;
        }
        else if (discriminant < -curvatureTolerance)
        {
            result.shape = SurfaceShape.Saddle;
        }
        else
        {
            result.shape = SurfaceShape.TroughOrRidge;
        }

        result.kind = DescribeKind(result.isCriticalPoint, result.shape);
        return result;
    }

    /// <summary>
    /// The two questions combined into the phrase the backend is handed.
    ///
    /// Note that "local minimum" and "local maximum" appear ONLY in the critical-point
    /// branch. Off a critical point the same curvature gets a description of the local
    /// shape and nothing more, because that is all the mathematics supports.
    /// </summary>
    private static string DescribeKind(bool isCritical, SurfaceShape shape)
    {
        if (isCritical)
        {
            switch (shape)
            {
                case SurfaceShape.Bowl:   return "local minimum";
                case SurfaceShape.Dome:   return "local maximum";
                case SurfaceShape.Saddle: return "saddle point";
                default:
                    return "critical point, second-derivative test inconclusive";
            }
        }

        switch (shape)
        {
            case SurfaceShape.Bowl:   return "sloping, curving upward in both directions";
            case SurfaceShape.Dome:   return "sloping, curving downward in both directions";
            case SurfaceShape.Saddle: return "sloping, with saddle-like curvature";
            case SurfaceShape.TroughOrRidge: return "sloping, with little curvature";
            default: return "undefined near this point";
        }
    }

    /// <summary>
    /// The wire name for a shape. Kept as an explicit switch rather than
    /// ToString().ToLower() so that renaming the enum cannot silently change the JSON
    /// contract - the backend validates these against a fixed set and would start
    /// rejecting every request.
    /// </summary>
    public static string WireName(SurfaceShape shape)
    {
        switch (shape)
        {
            case SurfaceShape.Bowl:          return "bowl";
            case SurfaceShape.Dome:          return "dome";
            case SurfaceShape.Saddle:        return "saddle";
            case SurfaceShape.TroughOrRidge: return "troughOrRidge";
            default:                         return "undefined";
        }
    }

    private static double Sample(GraphRenderer graph, double x, double y)
    {
        return graph.Evaluate((float)x, (float)y);
    }

    /// <summary>double.IsFinite exists in .NET Core but this keeps it obvious.</summary>
    private static bool IsFinite(double v)
    {
        return !double.IsNaN(v) && !double.IsInfinity(v);
    }
}
