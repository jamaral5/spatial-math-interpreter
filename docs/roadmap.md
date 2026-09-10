# Roadmap

Paced for roughly 5 hours a week.

The original plan had seven phases. What is committed here covers the *code* for
phases 1 through 6 — the plumbing, the payload, the prompt, the Unity display and the
persistence layer all exist and are wired together. What it does not cover is anything
requiring an AWS account, which is why Phase 0 has been added at the front and is now
the only thing standing between this and a working demo.

The phases below are therefore mostly **verification** rather than construction. That
is a deliberate reordering: writing the whole vertical slice first and proving it in
stages is far less frustrating than building one tier per fortnight and discovering at
week eight that the contract between two of them was wrong.

---

| Phase | Deliverable | Status | Estimate |
|---|---|---|---|
| **0. Accounts and tooling** | AWS account, budget alarm, IAM user, CLI + SAM + Docker, **one Playground call to enable the model** | **Not started — start here** | 1 week |
| 1. Plumbing | Stack deploys; `smoke-test` returns 200 | Code written, unverified | 1 session |
| 2. Real payload | Unity sends real geometry; Lambda parses it | Code written, unverified | 1 session |
| 3. Bedrock, static | A hardcoded example prompt returns real generated text | Code written, unverified | 1 session |
| 4. Bedrock, dynamic | Prompt built from Unity's live payload | Code written, unverified | 1–2 weeks |
| 5. Unity display | Explanation rendered in-scene beside the tangent readout | Code written, unverified | 1–2 weeks |
| 6. DynamoDB | Caching and history | Code written, unverified | 1 week |
| 7. Extended queries | "Where is the gradient largest?" and similar | Not started | Ongoing |

"Unverified" throughout means exactly that: the code exists and its dependency-free
logic has been exercised, but nothing has run against real AWS infrastructure, because
there is no account yet. Treat every one of those rows as *probably works, not yet
proven*.

---

## Phase 0 — accounts and tooling

The whole of [`getting-started.md`](getting-started.md).

Model enablement used to be the long pole here. It is not any more: AWS retired the
Model access page, and serverless foundation models are enabled automatically on first
invocation. What remains is that Anthropic models may ask a first-time user for
use-case details, and Marketplace-served models need one invocation by someone with
Marketplace permissions.

So the step is now ninety seconds in the Bedrock Playground rather than a wait for
approval — but do it deliberately, because the alternative is meeting the form as a
Lambda error later.

**Exit test:** a Claude model replies to you in the Bedrock Playground, in the region
you intend to deploy to. That is a stronger signal than `list-models.sh`, which reports
what the region *offers* rather than what your account can invoke.

---

## Phase 1 — plumbing

```bash
sam build && sam deploy && ./scripts/smoke-test.sh
```

**Exit test:** a JSON response with a populated `surfaceType`.

If it fails, the error code tells you where to look:

| `errorCode` | Meaning |
|---|---|
| `model_access_denied` | The model has never been invoked by this account, a use-case form is outstanding, or an IAM/SCP policy forbids it. Send one message in the Bedrock Playground. |
| `model_not_found` | `ModelId` in `samconfig.toml` is not one `list-models.sh` printed. |
| HTTP 403, no body | Missing or wrong API key. |
| HTTP 400, no body | The request validator rejected the payload before Lambda saw it. |

---

## Phase 2 — real payload

Copy the four files from `unity-client/` into `Assets/Scripts/`, add the two
components, paste the endpoint and key.

**Exit test:** turn on `logRequests` on the Math Interpreter Client, right-click the
surface, and confirm the JSON in the Unity console matches
`contracts/examples/saddle-on-a-slope.json` in shape.

---

## Phases 3 and 4 — Bedrock

These collapse into one step here, because the prompt is already built dynamically
from the payload. The "static prompt first" stage existed in the original plan to
de-risk the Bedrock call independently of the payload format; `smoke-test.sh` does that
job instead, since it posts a fixed known-good example.

**Exit test:** right-click the saddle at the origin and at `(1, 1)` on `x^2 - y^2`. The
origin should be described as a saddle point; `(1, 1)` should be described as sloping
and **must not** be called a maximum or minimum. That difference is the entire
mathematical honesty argument, and it is the single most valuable thing to check by
hand.

---

## Phase 5 — Unity display

Already built: `InterpretationPanel` has all four states (hidden, loading, ready,
failed).

**Exit tests:** disconnect the network mid-request and confirm a readable error rather
than a hung spinner; click rapidly on several points and confirm the panel shows the
*latest* answer, not whichever response happened to land last.

The one piece of the original Phase 5 not yet built is **highlighting the surface
region** on the mesh. `GraphRenderer` already writes per-vertex colours in `BuildMesh`,
so the natural approach is a second colour pass tinting vertices within some radius of
the clicked point — no shader work needed.

---

## Phase 6 — DynamoDB

Already built and deployed by the same template.

**Exit test:** run `smoke-test.sh` twice. First `"cached": false`, then
`"cached": true`. If the second still says false, check the CloudWatch logs for
`cache_write_failed` — the cache fails soft by design, so a broken cache costs money
silently rather than erroring.

---

## Phase 7 — extended queries

The interesting one, and where the architecture earns its keep.

"Show me where the gradient is largest" is answered by **Unity sampling gradient
magnitude across the mesh it already has** and sending only the winning point to be
explained. Same rule as everywhere else: the maths is deterministic and local, the
model only describes the result.

The backend change is small — a `queryType` field on the request, and a second prompt
variant. The Unity change is a sampling pass over the existing vertex grid.

Candidates, roughly in order of effort:

- Where is the surface steepest / flattest?
- Find all the critical points
- Compare two clicked points
- Walk the path of steepest descent from here

---

## Known gaps

Things deliberately left undone, recorded so they are choices rather than oversights.

- **Nothing has run against real AWS.** The single largest unknown.
- **The tests have never been executed.** They are written against the real modules,
  but `pytest` is not installed. `pip install -r requirements-dev.txt && pytest` is the
  first thing to do once tooling exists.
- **The API key is not a secret** — see the security section in
  [`architecture.md`](architecture.md). Cognito is the real answer, later.
- **No CI.** A GitHub Actions workflow running `pytest` and `sam validate` on push is
  maybe twenty lines and worth doing once the tests are known to pass.
- **Surface-region highlighting** is not built (Phase 5, above).
- **`ResolveGraph` uses a height-match heuristic** when several graphs share the scene.
  Exact would be threading the clicked `GraphRenderer` through
  `TangentPlaneRenderer.OnTangentPlaneShown`, which means editing a Unity file — see
  the note in `unity-client/README.md`.
- **Cold start** is roughly 1–2 s on the first call after idle, mostly the `anthropic`
  SDK import. A Lambda layer or provisioned concurrency would fix it; neither is worth
  it before there are users.
