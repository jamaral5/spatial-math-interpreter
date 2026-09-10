using UnityEngine;
using UnityEngine.UI;
using TMPro;

/// <summary>
/// Shows the generated explanation beneath the tangent readout.
///
/// Listens for the same event TangentReadout listens for, so the explanation appears
/// as part of the existing right-click -> "render tangent plane" flow rather than
/// needing an interaction of its own. Built at runtime in the same style as the rest
/// of the UI, and hidden until there is something to say.
///
/// The panel is a state machine with four states, and all four matter: this is now a
/// network call, and a UI that only handles the happy path will spend a lot of its
/// life looking broken on a bad connection.
///
///     hidden      nothing selected
///     loading     request in flight
///     ready       explanation shown
///     failed      something went wrong, with a reason the user can act on
/// </summary>
[AddComponentMenu("Spatial Math/Interpretation Panel")]
public class InterpretationPanel : MonoBehaviour
{
    [Header("References (found automatically if left empty)")]
    public TangentPlaneRenderer tangentPlaneRenderer;
    public MathInterpreterClient client;
    public Canvas targetCanvas;

    [Tooltip("Which graph to read the equation from. Leave empty with a single graph " +
             "on screen; see the class notes for the multi-graph case.")]
    public GraphRenderer graph;

    [Header("Layout")]
    public Vector2 panelSize = new Vector2(320f, 300f);

    [Tooltip("Inset from the top-right corner. The default clears TangentReadout, " +
             "which occupies the first 132 pixels below the top edge.")]
    public Vector2 panelOffset = new Vector2(-16f, -160f);

    private RectTransform panel;
    private TMP_Text statusText;
    private TMP_Text surfaceTypeText;
    private TMP_Text atPointText;
    private TMP_Text directionText;
    private RectTransform bodyColumn;

    private bool waiting;
    private float waitingSince;

    void Start()
    {
        // The same guard the rest of the UI scripts use, so a component accidentally
        // added twice does not build two overlapping panels.
        if (UIKit.IsDuplicate(this)) return;

        if (tangentPlaneRenderer == null)
            tangentPlaneRenderer = FindFirstObjectByType<TangentPlaneRenderer>();
        if (client == null)
            client = FindFirstObjectByType<MathInterpreterClient>();
        if (targetCanvas == null)
            targetCanvas = UIKit.FindSceneCanvas();

        if (tangentPlaneRenderer == null || client == null || targetCanvas == null)
        {
            Debug.LogWarning("[InterpretationPanel] Missing TangentPlaneRenderer, " +
                             "MathInterpreterClient or Canvas - disabling.");
            enabled = false;
            return;
        }

        Build();
        Hide();

        tangentPlaneRenderer.OnTangentPlaneShown += OnPlaneShown;
        tangentPlaneRenderer.OnTangentPlaneCleared += Hide;
    }

    void OnDestroy()
    {
        if (tangentPlaneRenderer == null) return;
        tangentPlaneRenderer.OnTangentPlaneShown -= OnPlaneShown;
        tangentPlaneRenderer.OnTangentPlaneCleared -= Hide;
    }

    void Update()
    {
        if (!waiting || statusText == null) return;

        // A spinner made of dots. Enough to show the app has not frozen, and it costs
        // nothing - a real animated sprite would need an asset and a canvas rebuild
        // every frame for the same information.
        int dots = 1 + (int)((Time.time - waitingSince) * 3f) % 3;
        statusText.text = "Interpreting" + new string('.', dots);
    }

    /// <summary>Live layout tweaking during Play mode, matching the other UI scripts.</summary>
    void OnValidate()
    {
        if (!Application.isPlaying || panel == null) return;
        UIKit.Corner(panel, new Vector2(1f, 1f), panelSize, panelOffset);
    }

    // ───────────────────────────────────────────────────────────────────
    // The flow
    // ───────────────────────────────────────────────────────────────────

    private void OnPlaneShown(Vector3 worldPoint, string tangentEquation, string derivatives)
    {
        GraphRenderer target = ResolveGraph(worldPoint);
        if (target == null)
        {
            ShowError("No graph found to read the equation from.");
            return;
        }

        // Measure first. The step size is taken from TangentPlaneRenderer rather than
        // configured separately, so the numbers in this panel can never disagree with
        // the plane drawn on the surface.
        SurfaceAnalyzer.Analysis analysis = SurfaceAnalyzer.Analyze(
            target, worldPoint, tangentPlaneRenderer.h);

        if (!analysis.valid)
        {
            ShowError("The surface is not defined near this point.");
            return;
        }

        ShowLoading();

        client.Interpret(
            analysis,
            target.GetCurrentEquation(),
            tangentEquation,
            tangentPlaneRenderer.h,
            target.graphRange,
            OnResponse);
    }

    private void OnResponse(InterpretResponseDto response)
    {
        // The panel may have been closed, or another point picked, while the request
        // was in the air.
        if (panel == null || !panel.gameObject.activeSelf) return;

        if (response == null)
        {
            ShowError("No response.");
            return;
        }

        if (response.IsError)
        {
            ShowError(response.errorMessage);
            return;
        }

        waiting = false;
        statusText.gameObject.SetActive(false);
        bodyColumn.gameObject.SetActive(true);

        surfaceTypeText.text = response.surfaceType;
        atPointText.text = response.atThisPoint;
        directionText.text = response.directionalBehavior;
    }

    /// <summary>
    /// Work out which graph was clicked.
    ///
    /// An explicit reference always wins. Failing that, every graph in the scene is
    /// asked how high it is at the clicked x and y, and the one that best matches the
    /// clicked height is the one the ray actually hit. That keeps this correct once
    /// several equations share the screen, without needing the selector to hand the
    /// graph along a chain of events that currently does not carry it.
    /// </summary>
    private GraphRenderer ResolveGraph(Vector3 worldPoint)
    {
        if (graph != null) return graph;

        GraphRenderer[] all = FindObjectsByType<GraphRenderer>(FindObjectsSortMode.None);
        if (all.Length == 0) return null;
        if (all.Length == 1) return all[0];

        GraphRenderer best = null;
        float bestError = float.MaxValue;

        foreach (GraphRenderer candidate in all)
        {
            float height = candidate.Evaluate(worldPoint.x, worldPoint.z);
            if (float.IsNaN(height) || float.IsInfinity(height)) continue;

            float error = Mathf.Abs(height - worldPoint.y);
            if (error >= bestError) continue;

            bestError = error;
            best = candidate;
        }

        return best ?? all[0];
    }

    // ───────────────────────────────────────────────────────────────────
    // States
    // ───────────────────────────────────────────────────────────────────

    private void ShowLoading()
    {
        panel.gameObject.SetActive(true);
        bodyColumn.gameObject.SetActive(false);
        statusText.gameObject.SetActive(true);
        statusText.color = UIKit.InkDim;
        statusText.text = "Interpreting.";

        waiting = true;
        waitingSince = Time.time;
    }

    private void ShowError(string message)
    {
        panel.gameObject.SetActive(true);
        bodyColumn.gameObject.SetActive(false);
        statusText.gameObject.SetActive(true);
        statusText.color = new Color(1f, 0.45f, 0.4f);
        statusText.text = string.IsNullOrEmpty(message) ? "Something went wrong." : message;

        waiting = false;
    }

    private void Hide()
    {
        waiting = false;
        if (panel != null) panel.gameObject.SetActive(false);
    }

    // ───────────────────────────────────────────────────────────────────
    // Construction
    // ───────────────────────────────────────────────────────────────────

    private void Build()
    {
        panel = UIKit.NeonPanel("InterpretationPanel", targetCanvas.transform,
                                UIKit.PanelDark, UIKit.NeonSoft);
        UIKit.Corner(panel, new Vector2(1f, 1f), panelSize, panelOffset);

        RectTransform root = UIKit.Stretch(UIKit.NewRect("Column", panel), 14f);
        var rootLayout = root.gameObject.AddComponent<VerticalLayoutGroup>();
        rootLayout.childAlignment = TextAnchor.UpperLeft;
        rootLayout.childControlWidth = true;
        rootLayout.childControlHeight = true;
        rootLayout.childForceExpandWidth = true;
        rootLayout.childForceExpandHeight = false;
        rootLayout.spacing = 4f;

        AddHeading(root, "What this means");

        // The loading and error states share one text object. They are mutually
        // exclusive and both replace the body, so two objects would only mean two
        // things to remember to hide.
        statusText = UIKit.Label("Status", root, "", 13f, UIKit.InkDim,
                                 TextAlignmentOptions.TopLeft);
        statusText.gameObject.AddComponent<LayoutElement>().preferredHeight = 20f;

        bodyColumn = UIKit.NewRect("Body", root);
        var bodyLayout = bodyColumn.gameObject.AddComponent<VerticalLayoutGroup>();
        bodyLayout.childControlWidth = true;
        bodyLayout.childControlHeight = true;
        bodyLayout.childForceExpandWidth = true;
        bodyLayout.childForceExpandHeight = false;
        bodyLayout.spacing = 6f;

        surfaceTypeText = AddParagraph(bodyColumn, UIKit.Ink);
        atPointText = AddParagraph(bodyColumn, UIKit.Ink);
        directionText = AddParagraph(bodyColumn, UIKit.InkDim);
    }

    private static void AddHeading(Transform parent, string text)
    {
        TextMeshProUGUI heading = UIKit.Label("Heading", parent, text.ToUpperInvariant(),
                                             11f, UIKit.Neon, TextAlignmentOptions.Left);
        heading.characterSpacing = 6f;
        heading.gameObject.AddComponent<LayoutElement>().preferredHeight = 16f;
    }

    /// <summary>
    /// A wrapping paragraph whose height follows its content.
    ///
    /// The explanation is generated text of unpredictable length, so a fixed height
    /// would either clip it or leave a gap. A preferred height of -1 tells the layout
    /// group to ask the text for its own height instead.
    /// </summary>
    private static TMP_Text AddParagraph(Transform parent, Color color)
    {
        TextMeshProUGUI text = UIKit.Label("Paragraph", parent, "", 13f, color,
                                           TextAlignmentOptions.TopLeft);
        text.textWrappingMode = TextWrappingModes.Normal;

        var element = text.gameObject.AddComponent<LayoutElement>();
        element.preferredHeight = -1f;
        element.flexibleHeight = 0f;

        return text;
    }
}
