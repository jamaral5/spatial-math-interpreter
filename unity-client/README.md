# Unity client

Four C# files that live here rather than in the Unity project, because this backend is
a separate repository and dropping files into `Assets/` before the backend exists would
leave the Unity project unable to compile.

**They are staged, not installed.** Copy them across when you reach Phase 2.

| File | Does |
|---|---|
| `SurfaceAnalyzer.cs` | The mathematics. Second derivatives, discriminant, classification. No networking. |
| `InterpretDto.cs` | The wire format. `JsonUtility`-shaped request and response types. |
| `MathInterpreterClient.cs` | The networking. `UnityWebRequest` POST, cancellation, error mapping. No mathematics. |
| `InterpretationPanel.cs` | The UI. Four states, built at runtime in the existing `UIKit` style. |

The split between the first and third is the architecture in miniature: numbers are
computed in C#, words are generated in the cloud, and neither file does the other's
job.

---

## Installing

1. Copy all four `.cs` files into `Assets/Scripts/`. Unity will generate `.meta` files
   and recompile.
2. Add **Math Interpreter Client** to any GameObject in the scene. One instance is
   enough.
3. Add **Interpretation Panel** to any GameObject. It finds its own references and
   builds its own UI at runtime, exactly like `TangentReadout` does.
4. On the Math Interpreter Client, fill in two fields:
   - **Endpoint** — the `ApiEndpoint` output from `sam deploy`. Ends in `/interpret`.
   - **Api Key** — from `scripts/get-api-key.sh`.
5. Press Play and right-click the surface.

Nothing else needs wiring. The panel subscribes to
`TangentPlaneRenderer.OnTangentPlaneShown`, the same event `TangentReadout` already
uses, so the explanation appears as part of the existing right-click → *render tangent
plane* flow rather than needing an interaction of its own.

---

## How it hooks into what already exists

```
SurfacePointSelector          right-click, raycast, nearest graph
        │
        ▼
TangentPlaneRenderer          computes f, fx, fy; draws plane and lines
        │
        │  OnTangentPlaneShown(worldPoint, tangentEquation, derivatives)
        ├──────────────────► TangentReadout          (already there)
        │
        └──────────────────► InterpretationPanel     (new)
                                    │
                                    ├── SurfaceAnalyzer.Analyze(graph, point, h)
                                    │       9-point stencil, second derivatives,
                                    │       discriminant, classification
                                    │
                                    └── MathInterpreterClient.Interpret(...)
                                            POST → AWS → panel
```

`InterpretationPanel` reads the step size `h` straight off `TangentPlaneRenderer`
rather than having its own setting, so the numbers in the explanation can never
disagree with the plane drawn on the surface.

---

## Things worth knowing before you edit these

### `JsonUtility` has three rules that will bite you

1. **Only public fields serialise.** A `{ get; set; }` property is silently skipped —
   producing a request missing half its data, and a gateway error that does not
   obviously point back at the DTO.
2. **The C# field name *is* the JSON key.** There is no rename attribute. That is why
   the whole API is camelCase rather than the snake_case a Python backend would
   normally use.
3. **Every field is always written.** There is no way to express "absent". Where that
   matters — the steepest-ascent heading, meaningless on level ground — the *backend*
   decides whether to use the value, gating on `isCriticalPoint` rather than on the
   field being missing.

The alternative was adding Newtonsoft.Json. Not worth a package dependency for six
flat objects.

### Do not use `UnityWebRequest.Post(url, string)`

That convenience overload **form-encodes** the string it is given. The body arrives as
`application/x-www-form-urlencoded` with the JSON mangled into a field name, API
Gateway rejects it against the request model, and the resulting 400 says nothing about
the real cause.

`MathInterpreterClient` builds the request by hand with an `UploadHandlerRaw` for
exactly this reason. If you refactor it, keep that.

### Coordinates

Unity's axes are not the maths axes, and getting this backwards is the easiest mistake
in the project:

| Maths | Unity world |
|---|---|
| `x` (first input) | `X` |
| `y` (second input) | `Z` |
| `f(x, y)` (output) | `Y` |

So `graph.Evaluate(worldPoint.x, worldPoint.z)` gives the height, and the payload's
`point.y` is `worldPoint.z`. This matches the convention `TangentPlaneRenderer` already
documents.

### Multiple graphs on screen

`InterpretationPanel.ResolveGraph` uses a heuristic: it asks every `GraphRenderer` in
the scene how high it is at the clicked x and y, and picks the one whose height best
matches the clicked point. That is correct in practice and needs no changes to existing
files.

The exact fix is to thread the clicked `GraphRenderer` through the event, since
`SurfacePointSelector` already knows it and `TangentPlaneRenderer` already stores it.
Two edits to an existing file:

```csharp
// TangentPlaneRenderer.cs — expose the graph the last tangent plane was read from
public GraphRenderer CurrentGraph => graphRenderer;
```

then in `InterpretationPanel.ResolveGraph`, prefer
`tangentPlaneRenderer.CurrentGraph` before falling back to the heuristic.

Left undone on purpose: the Unity repository's roadmap still lists "multiple equations
on screen at once" as unbuilt, so there is currently nothing to be wrong about, and
these files are meant to drop in without touching anything that already works.

### The API key

It ships inside the build and is therefore **not a secret** — anyone with the build can
extract it. It is a meter, not a password: the usage plan attached to it enforces a
rate limit and a hard monthly quota, so a leaked key caps what it can cost rather than
opening an unbounded bill.

Do not commit it. See the security section of [`../docs/architecture.md`](../docs/architecture.md).

---

## Testing without the backend

`MathInterpreterClient` returns a clean `not_configured` error when the endpoint is
empty, so you can add the components and press Play before anything is deployed — the
panel shows the reason instead of hanging.

`SurfaceAnalyzer` has no networking at all, so its output can be checked in isolation:

```csharp
var a = SurfaceAnalyzer.Analyze(graph, new Vector3(0f, 0f, 0f));
Debug.Log($"{a.kind}  D={a.discriminant}  critical={a.isCriticalPoint}");
```

On `x^2 - y^2` at the origin that should print a saddle point with `D ≈ -4` and
`critical=True`. At `(1, 1)` on the same surface it should print *sloping* with the
same `D` and `critical=False` — and that difference is the whole mathematical honesty
argument, so it is the first thing worth checking by hand.
