using System;

/// <summary>
/// The wire format shared with the AWS backend.
///
/// These types exist to be handed to JsonUtility, which imposes three rules worth
/// knowing before editing anything here:
///
///   1. Only PUBLIC FIELDS are serialised. A property with { get; set; } is silently
///      skipped, which produces a request that is missing half its data and an error
///      from the gateway that does not obviously point back to this file.
///
///   2. The C# field name IS the JSON key. There is no rename attribute, so the field
///      names below are the contract - which is why the whole API is camelCase rather
///      than the snake_case that would be more usual for a Python backend.
///
///   3. Every field is always written. JsonUtility cannot omit one, so there is no way
///      to express "this value is absent". Where that matters - the steepest-ascent
///      heading, which is meaningless on level ground - the backend decides whether to
///      use the value rather than expecting it to be missing.
///
/// The alternative was to add Newtonsoft.Json to the project. Not worth a package
/// dependency for six flat objects.
/// </summary>
[Serializable]
public class InterpretRequestDto
{
    /// <summary>Bumped only for a breaking shape change. The backend logs a mismatch.</summary>
    public string schemaVersion = "1.0";

    public string equation;
    public PointDto point = new PointDto();
    public GradientDto gradient = new GradientDto();
    public CurvatureDto curvature = new CurvatureDto();
    public ClassificationDto classification = new ClassificationDto();
    public TangentPlaneDto tangentPlane = new TangentPlaneDto();
    public SamplingDto sampling = new SamplingDto();

    /// <summary>
    /// Fills a request straight from a SurfaceAnalyzer result.
    ///
    /// Everything here is a copy, never a calculation. If a number appears in this
    /// method that did not come out of the Analysis struct, something has gone wrong
    /// with the design.
    /// </summary>
    public static InterpretRequestDto From(
        SurfaceAnalyzer.Analysis analysis,
        string equation,
        string tangentPlaneEquation,
        float stepH,
        float domainRange)
    {
        var dto = new InterpretRequestDto();

        dto.equation = equation;

        dto.point.x = analysis.x;
        dto.point.y = analysis.y;
        dto.point.f = analysis.f;

        dto.gradient.dfdx = analysis.fx;
        dto.gradient.dfdy = analysis.fy;
        dto.gradient.magnitude = analysis.gradientMagnitude;
        dto.gradient.steepestAscentHeadingDegrees = analysis.headingDegrees;

        dto.curvature.d2fdx2 = analysis.fxx;
        dto.curvature.d2fdy2 = analysis.fyy;
        dto.curvature.d2fdxdy = analysis.fxy;
        dto.curvature.discriminant = analysis.discriminant;

        dto.classification.kind = analysis.kind;
        dto.classification.shape = SurfaceAnalyzer.WireName(analysis.shape);
        dto.classification.isCriticalPoint = analysis.isCriticalPoint;

        dto.tangentPlane.equation = tangentPlaneEquation ?? "";

        dto.sampling.stepH = stepH;
        dto.sampling.domainRange = domainRange;
        dto.sampling.criticalTolerance = SurfaceAnalyzer.DefaultCriticalTolerance;
        dto.sampling.curvatureTolerance = SurfaceAnalyzer.DefaultCurvatureTolerance;

        return dto;
    }
}

[Serializable]
public class PointDto
{
    // Maths coordinates, not Unity's. x and y are the two inputs, f is the height.
    public float x;
    public float y;
    public float f;
}

[Serializable]
public class GradientDto
{
    public float dfdx;
    public float dfdy;
    public float magnitude;

    /// <summary>
    /// Direction of steepest ascent, degrees anticlockwise from the +x axis.
    /// Ignored by the backend at a critical point, where it is only noise.
    /// </summary>
    public float steepestAscentHeadingDegrees;
}

[Serializable]
public class CurvatureDto
{
    public float d2fdx2;
    public float d2fdy2;
    public float d2fdxdy;
    public float discriminant;
}

[Serializable]
public class ClassificationDto
{
    /// <summary>The finished label, e.g. "saddle point". Written by SurfaceAnalyzer.</summary>
    public string kind;

    /// <summary>One of: bowl, dome, saddle, troughOrRidge, undefined.</summary>
    public string shape;

    /// <summary>Whether the ground is level here. Gates the max/min claim entirely.</summary>
    public bool isCriticalPoint;
}

[Serializable]
public class TangentPlaneDto
{
    public string equation;
}

[Serializable]
public class SamplingDto
{
    public float stepH;
    public float domainRange;
    public float criticalTolerance;
    public float curvatureTolerance;
}

/// <summary>
/// What comes back.
///
/// Success and failure share one flat shape - on an error the three explanation
/// fields are empty and errorCode is set. That is deliberate: JsonUtility cannot
/// express "either this shape or that one", so a single layout with one field to
/// check is far less fragile than trying to detect which of two shapes arrived.
/// </summary>
[Serializable]
public class InterpretResponseDto
{
    public string schemaVersion;

    // The three parts of the explanation, in the order the panel displays them.
    public string surfaceType;
    public string atThisPoint;
    public string directionalBehavior;

    /// <summary>
    /// Echoed back from this client's own payload, never taken from the model's prose.
    /// The label shown in the UI is always the one the C# second-derivative test
    /// produced, whatever the generated text happens to say.
    /// </summary>
    public string classificationKind;

    /// <summary>True when the backend served this from DynamoDB without calling the model.</summary>
    public bool cached;

    public string modelId;
    public string promptVersion;
    public string requestId;

    /// <summary>Empty on success. Set to a stable machine-readable code on failure.</summary>
    public string errorCode;
    public string errorMessage;

    public bool IsError => !string.IsNullOrEmpty(errorCode);
}
