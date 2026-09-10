# Architecture

## The rule everything else follows

**Unity computes every number. The model computes none.**

That is not a style preference, it is the load-bearing decision of the whole design.
Language models are unreliable arithmetic engines and reliable explainers, so the
system is arranged to only ever ask one of them for the thing it is good at.

The naive version of this project — send the equation and the click coordinates to a
model and ask "what's happening here?" — produces a demo that works most of the time
and is wrong in ways a student cannot detect. Wrong in a maths teaching tool is worse
than useless.

What makes the rule enforceable rather than aspirational is that it is structural in
four separate places:

1. **The model is handed the answer, not the problem.** It receives `f`, both partial
   derivatives, all three second partials, the discriminant, and a finished
   classification. There is no calculation left for it to get wrong because there is no
   calculation left.
2. **Its output is schema-pinned to three string fields.** `additionalProperties:
   false` means it cannot return a number as a structured value. Anything it invents
   has to appear inside prose, where it is visible, rather than in a field the UI would
   render as fact.
3. **The classification returned to Unity is echoed from Unity's own payload.** The
   Lambda never reads a classification out of the generated text. Whatever the prose
   says, the label the panel displays is the one the C# second-derivative test
   produced. There is a test named after this.
4. **The equation is treated as untrusted input.** It is student-typed text that ends
   up inside a prompt, so it passes a character allowlist, is rendered inside a
   delimited block the system prompt names as data, and cannot change the response
   shape even if an injection succeeded.

---

## Shape of the system

```
UNITY  (C#)                      AWS                            BEDROCK
─────────────                    ─────────────                  ─────────────
EquationParser                                                  
GraphRenderer.Evaluate                                          
     │                                                          
     ▼                                                          
SurfaceAnalyzer                  API Gateway (REST)             
  9-point stencil                  · API key + usage plan       
  ∂f/∂x  ∂f/∂y                     · request validator          
  ∂²f/∂x² ∂²f/∂y² ∂²f/∂x∂y         │                            
  D = fxx·fyy − fxy²                ▼                           
  classification                  Lambda (Python 3.12)          
     │                              │                           
     │  JSON  ──────────────────►   ├── cache lookup ──► DynamoDB
     │                              │                     (hit: return)
     │                              │                           
     │                              ├── build prompt            
     │                              │                           
     │                              └── messages.create  ──────►  Claude
     │                                    · system (cached)       (interpretation
     │                                    · structured output      only)
     │                                                      ◄──── 
     │                              ┌── cache write ──► DynamoDB
     ◄──────────────────────────────┘
InterpretationPanel
  loading / ready / failed
```

The ordering inside the Lambda is the design: **the cache sits in front of the model,
not behind it**, because the model call is the only step that costs money per request.
Everything before it is free.

---

## Decisions worth defending

### REST API Gateway, not HTTP API

HTTP API is about $2.50 per million requests cheaper and simpler to configure. REST
was chosen anyway for one reason: **only REST supports request validators**. A
malformed payload is rejected at the edge and never becomes a Lambda invocation.

That is the behaviour the original design called for — validate before it costs
anything — and HTTP API would forward every piece of junk straight to the function.
At this volume the price difference is a rounding error; the architectural property is
worth more.

### The Anthropic Bedrock Mantle client, not boto3 Converse

boto3 ships in the Lambda runtime, so using it would add nothing to the bundle. The
Mantle client costs roughly 20 MB and a slower cold start.

It wins anyway because Converse is a lowest-common-denominator surface across every
Bedrock vendor, which means the two features this service is built on — structured
output and the effort setting — have to be smuggled through
`additionalModelRequestFields` as untyped JSON. The Mantle client speaks the Messages
API directly, so both are first-class. If this project ever moves off Bedrock, the
change is the client constructor and nothing else.

For an endpoint fired by a human clicking a surface, 200 ms of cold start is the right
thing to spend.

### One DynamoDB table, two access patterns

| Pattern | Mechanism |
|---|---|
| "Have I explained this exact point before?" | `get_item` on the partition key |
| "What has been asked about this equation?" | Query the `equation-createdAt-index` GSI |

The partition key is a SHA-256 over everything that could change the answer —
including the model id and prompt version, so that changing either retires the old
entries instead of serving wording the current prompt would never produce.

**The key is rounded to two decimal places, deliberately matching the UI.**
`TangentReadout` and the tangent-plane label both format with `F2`, so the student sees
two decimals. If two clicks are indistinguishable on screen they describe the same
situation and deserve the same explanation — which turns a stream of near-misses around
one interesting feature of a surface into repeated cache hits.

Written with the raw `AWS::DynamoDB::Table` resource rather than SAM's `SimpleTable`,
because `SimpleTable` supports neither TTL nor a secondary index and this needs both.

### The cache fails soft

Every function in `store.py` swallows its errors and logs. A cache is an optimisation,
and an optimisation that can take down the endpoint is a liability. If DynamoDB is
unavailable the request is still answered — just more expensively.

### camelCase on the wire

Unity's `JsonUtility` maps JSON keys onto C# field names verbatim, with no rename
attribute. snake_case on the wire would force snake_case field names through the entire
C# client. camelCase keeps both sides idiomatic, and the Python side reads the keys
explicitly in `payload.py` rather than relying on any automatic mapping.

The alternative was adding Newtonsoft.Json to the Unity project. Not worth a package
dependency for six flat objects.

### One response shape for success and failure

`JsonUtility` cannot express a discriminated union or an optional nested object. So an
error response carries the same keys as a success, with the three explanation fields
empty and `errorCode` set.

That means the C# client has exactly one DTO and checks exactly one field, instead of
attempting to detect which of two layouts arrived. There is a test asserting the two
shapes have identical keys.

### Lazy Bedrock client

Constructing it resolves AWS credentials. At import time, a credential problem becomes
an unhandled exception during initialisation, which Lambda reports as an opaque runtime
error with no request context. Built lazily inside the handler's call path, the same
problem surfaces where it can be caught and returned as a clean message — and the
module stays importable in a test that never intends to call Bedrock.

It is still built once per container and reused, which is the property that actually
mattered.

---

## The mathematics, and where it is honest

`SurfaceAnalyzer.cs` samples a 3×3 stencil around the clicked point — nine evaluations,
the smallest set that yields all three second derivatives by central differences:

```
fx  = (f(x+h,y) − f(x−h,y)) / 2h
fy  = (f(x,y+h) − f(x,y−h)) / 2h
fxx = (f(x+h,y) − 2f(x,y) + f(x−h,y)) / h²
fyy = (f(x,y+h) − 2f(x,y) + f(x,y−h)) / h²
fxy = (f(x+h,y+h) − f(x+h,y−h) − f(x−h,y+h) + f(x−h,y−h)) / 4h²
D   = fxx·fyy − fxy²
```

All of it runs in `double` even though `Evaluate` returns `float`. The second
derivatives are built from differences of nearly equal numbers divided by `h²`, which
is the classic recipe for cancellation error, and the extra precision costs nothing at
nine evaluations per click.

### The part that is easy to get wrong

The second-derivative test only identifies a maximum or a minimum **at a critical
point** — somewhere the ground is level. Halfway down a hillside, a bowl-shaped patch
of surface is still a hillside. Calling it a "local minimum" would be plainly false.

So the classification is deliberately **two separate questions**:

| Question | Answered by |
|---|---|
| Is the ground level here? | `isCriticalPoint`, from the gradient magnitude |
| Which way does it curve? | `shape`, from the discriminant |

and only when the first is *yes* does the second become a claim about a maximum or a
minimum. Off a critical point, the same curvature earns a description of local shape
and nothing more.

This propagates all the way through: the system prompt states the caveat explicitly,
and the heading of steepest ascent is **withheld entirely** at a critical point,
because there the gradient is numerical noise and reporting its direction would mean
describing noise as "due east".

Being straight about what the mathematics does not settle is worth more to a student
than a confident label that is wrong.

### Tolerances

Neither test can compare against exact zero, because `fx` and `fy` are numerical
estimates that come back as small non-zero noise even at a true critical point.

- `criticalTolerance = 0.01` — too small and the exact top of a hill is never
  recognised as a maximum; too large and a gentle but real slope gets called flat.
- `curvatureTolerance = 0.001` — looser than it looks, because second derivatives
  divide by `h²`, which amplifies their error.

Both are exposed as constants and sent in the payload, so a surprising answer can be
traced back to the settings that produced it.

---

## Security posture, stated honestly

**The API key is not a secret.** It ships inside a client build, and anyone with the
build can extract it. No amount of obfuscation changes that.

It is worth having because it is not doing the job of a password — it is doing the job
of a **meter**. The usage plan attached to it enforces a request rate and a hard
monthly quota, so a leaked key caps what it can cost rather than opening an unbounded
bill. Bedrock is billed per call, and that is the actual risk being managed.

The real fix, when this stops being a student project, is Cognito or a signed
short-lived token issued per session. That is a later problem, and building auth before
there is anything to authenticate would be the wrong order.

### Prompt injection

Three layers, because the first one alone is not sufficient:

1. **Character allowlist** on the equation, matching the grammar Unity's parser
   accepts. Blocks punctuation-heavy injections. Does *not* block
   `ignore previous instructions`, which is unfortunately all letters and spaces.
2. **Delimited data block.** The equation is rendered inside `<surface-data>` tags that
   the system prompt explicitly names as untrusted content to be described, never
   obeyed.
3. **Structured output.** Even a fully successful injection cannot change the response
   shape — three string fields, no more — so it cannot alter what the UI renders as
   fact, only what the prose says inside a field the student can read.
