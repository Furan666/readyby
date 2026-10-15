# Product feedback and observed friction

## Tools actually used

Official MCP Python SDK2.3.0 provides a real Streamable HTTP endpoint. The included JavaScript browser client initializes protocol2025-11-25, discovers/calls tools and consumes structured results. OR-Tools9.15.6755 handles shared-resource scheduling and bounded infeasibility proofs; SQLite preserves reported progress and review revisions.

No Alexa+ preview, Amazon device or AWS service was used. This feedback describes an MCP implementation for the published Alexa+ contest route, not an experience with an Amazon service we have not accessed.

## Onboarding / what worked

The official SDK installed in a separate Python3.14 environment. Its custom routes let the same local process serve both the review client and the MCP endpoint. In-process HTTP tests negotiated the required protocol and called actual registered tools without a mock server. OR-Tools optional intervals, NoOverlap and assumptions map directly onto tasks, shared resources and mandatory conflicts.

## Friction log — actual reproduction

Task: consume a dictionary tool result as structuredContent. Steps: annotate a tool `-> dict`, initialize the real endpoint, call it. Expected: a structured JSON object for the client. Actual: only text content was returned. Forcing `structured_output=True` then failed registration with InvalidSignature because bare dict is not a supported structured schema. Workaround: annotate `-> dict[str, Any]` and explicitly enable structured output. The protocol regressions then passed. Severity: moderate onboarding friction. Suggestion: point the bare-dict registration diagnostic directly to the generic-dictionary / TypedDict example, or issue a warning when a JSON dictionary silently becomes text only.

The local review endpoint's token/body-limit bugs were our application mistakes, found and repaired by tests; they are not reported as SDK defects. Similarly, the first large file-write command exceeded the local Windows shell argument limit; splitting writes resolved it and is not an Amazon defect.

## Would we build again?

Yes, with the official MCP SDK and OR-Tools for this bounded planning role. Production use would require stronger multi-user authentication, actual platform integration, field testing and measured task-duration uncertainty. No such validation is claimed here.
