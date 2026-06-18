# Parser hardening v10

Problem found by Biopsy: a model emitted an opening `<function_calls>` block with a very large `file_write` payload, but the response was truncated before a valid JSON block was complete. The app then tried to parse the whole response as ordinary JSON and failed.

v10 behavior:

1. Execute valid `<function_calls>...</function_calls>` blocks before trusting final answers.
2. If a function-call block is present but invalid or truncated, do **not** parse/trust later text.
3. Add a compact `parse_repair` event to the transcript.
4. Retry the model with clear feedback: return one compact JSON action, do not inline thousands of digits or giant datasets, and write code that computes data at runtime.
5. Keep the failure visible in Biopsy.

Practical rule for coding agents: tool payloads should be small. For π/e digit work, code should compute digits with `mpmath` if available, not paste thousands of digits into JSON.
