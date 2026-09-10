using System;
using System.Collections;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// Sends a measured point to the AWS backend and hands back the explanation.
///
/// This file contains no mathematics, on purpose. It receives a finished
/// SurfaceAnalyzer.Analysis, packages it, posts it, and parses what comes back. The
/// split is the architecture: numbers are computed in C#, words are generated in the
/// cloud, and neither side does the other's job.
///
/// Attach to any GameObject in the scene - one instance is enough.
/// </summary>
[AddComponentMenu("Spatial Math/Math Interpreter Client")]
public class MathInterpreterClient : MonoBehaviour
{
    [Header("Endpoint")]
    [Tooltip("The ApiEndpoint output from `sam deploy`. Ends in /interpret.")]
    public string endpoint = "";

    [Tooltip("API key from scripts/get-api-key.sh. See the note in the class summary " +
             "about what this key does and does not protect.")]
    public string apiKey = "";

    [Header("Behaviour")]
    [Tooltip("Give up after this long. The backend's own budget is 30s, so waiting " +
             "much longer than this only holds a spinner on screen.")]
    public float timeoutSeconds = 20f;

    [Tooltip("Log the outgoing JSON to the console. Useful the first time you wire " +
             "this up, noisy afterwards.")]
    public bool logRequests = false;

    // ─── A NOTE ON THE API KEY ──────────────────────────────────────────
    // A key shipped inside a client build is not a secret. Anyone with the build can
    // extract it, and no amount of obfuscation changes that.
    //
    // It is still worth having, because it is not doing the job of a password - it is
    // doing the job of a meter. The usage plan attached to it enforces a request rate
    // and a hard monthly quota, so a leaked key caps what it can cost rather than
    // opening an unbounded bill. That is the actual risk being managed here: Bedrock
    // is billed per call.
    //
    // The real fix, when this stops being a student project, is Cognito or a signed
    // short-lived token issued per session. That is a Phase 8 problem, not a Phase 1
    // one, and pretending otherwise would mean building auth before there is anything
    // to authenticate.
    // ────────────────────────────────────────────────────────────────────

    private UnityWebRequest inFlight;

    void OnDestroy()
    {
        // A request still running when the scene unloads will try to invoke a callback
        // on a destroyed object. Abort explicitly rather than relying on luck.
        CancelInFlight();
    }

    /// <summary>Whether a request is currently outstanding. Drives the spinner.</summary>
    public bool IsBusy => inFlight != null;

    /// <summary>
    /// Ask the backend to explain a point.
    ///
    /// Any request already running is cancelled first. Clicking around a surface
    /// generates requests faster than they complete, and without this the panel would
    /// flicker between answers as older responses landed after newer ones.
    /// </summary>
    public void Interpret(
        SurfaceAnalyzer.Analysis analysis,
        string equation,
        string tangentPlaneEquation,
        float stepH,
        float domainRange,
        Action<InterpretResponseDto> onComplete)
    {
        if (onComplete == null) return;

        if (string.IsNullOrWhiteSpace(endpoint))
        {
            onComplete(Failure("not_configured",
                "No endpoint set. Paste the ApiEndpoint output from `sam deploy` into " +
                "the Math Interpreter Client component."));
            return;
        }

        // An undefined point has nothing to explain, and sending it would only earn a
        // 400 from the backend's own finite-number check. Fail here and save the trip.
        if (!analysis.valid)
        {
            onComplete(Failure("undefined_point",
                "The surface is not defined near this point."));
            return;
        }

        CancelInFlight();

        InterpretRequestDto dto = InterpretRequestDto.From(
            analysis, equation, tangentPlaneEquation, stepH, domainRange);

        StartCoroutine(Send(JsonUtility.ToJson(dto), onComplete));
    }

    private IEnumerator Send(string json, Action<InterpretResponseDto> onComplete)
    {
        if (logRequests) Debug.Log($"[MathInterpreter] POST {endpoint}\n{json}");

        // Built by hand rather than with UnityWebRequest.Post(url, string).
        //
        // That convenience overload FORM-ENCODES the string it is given - the body
        // arrives as application/x-www-form-urlencoded with the JSON mangled into a
        // field name. API Gateway then rejects it against the request model, and the
        // resulting 400 says nothing about the real cause. Raw upload handler it is.
        var request = new UnityWebRequest(endpoint, UnityWebRequest.kHttpVerbPOST);
        request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
        request.downloadHandler = new DownloadHandlerBuffer();
        request.SetRequestHeader("Content-Type", "application/json");
        request.timeout = Mathf.Max(1, Mathf.RoundToInt(timeoutSeconds));

        if (!string.IsNullOrWhiteSpace(apiKey))
            request.SetRequestHeader("x-api-key", apiKey);

        inFlight = request;

        yield return request.SendWebRequest();

        // Cancelled by a newer click. The newer request owns the panel now, so this
        // one must return quietly rather than overwrite it with a stale answer.
        if (inFlight != request)
        {
            request.Dispose();
            yield break;
        }

        inFlight = null;

        InterpretResponseDto response = Interpret(request);
        request.Dispose();

        onComplete(response);
    }

    /// <summary>
    /// Turn a finished UnityWebRequest into a response object.
    ///
    /// The body is parsed even on a 4xx or 5xx, because the backend returns its errors
    /// in the same JSON shape as its successes - so a validation failure arrives with
    /// a message worth showing the user rather than a bare status code.
    /// </summary>
    private InterpretResponseDto Interpret(UnityWebRequest request)
    {
        string body = request.downloadHandler != null ? request.downloadHandler.text : null;

        if (!string.IsNullOrEmpty(body))
        {
            try
            {
                InterpretResponseDto parsed = JsonUtility.FromJson<InterpretResponseDto>(body);
                if (parsed != null) return parsed;
            }
            catch (Exception e)
            {
                // Falls through to the transport-level error below. A body that will
                // not parse is usually API Gateway's own error page, which is not in
                // our schema - most often a missing or wrong API key.
                Debug.LogWarning($"[MathInterpreter] Could not parse response: {e.Message}\n{body}");
            }
        }

        switch (request.result)
        {
            case UnityWebRequest.Result.ConnectionError:
                return Failure("network_error", "Could not reach the server.");

            case UnityWebRequest.Result.ProtocolError:
                return Failure(
                    "http_" + request.responseCode,
                    request.responseCode == 403
                        ? "Rejected by the API (403). Usually a missing or wrong API key."
                        : $"The server returned {request.responseCode}.");

            case UnityWebRequest.Result.DataProcessingError:
                return Failure("bad_response", "The server's reply could not be read.");

            default:
                return Failure("empty_response", "The server returned nothing.");
        }
    }

    private void CancelInFlight()
    {
        if (inFlight == null) return;

        UnityWebRequest request = inFlight;
        inFlight = null;      // cleared first, so the coroutine sees it is orphaned
        request.Abort();
    }

    private static InterpretResponseDto Failure(string code, string message)
    {
        return new InterpretResponseDto
        {
            schemaVersion = "1.0",
            surfaceType = "",
            atThisPoint = "",
            directionalBehavior = "",
            classificationKind = "",
            cached = false,
            errorCode = code,
            errorMessage = message,
        };
    }
}
