# v11 Generic Atomic Controller

v11 is not a one-off fix for any specific prompt. It changes the agent loop so broad work is decomposed into smaller, observable units.

## Problem this solves

Earlier versions could fail when the model tried to do too much in one response:

- paste a huge generated file into a JSON tool call
- emit many tool calls at once
- return prose plus broken tool-call markup
- claim it ran code without a `python_run` result
- carry giant raw transcripts forward into the next prompt

Those are not separate bugs. They are the same architectural problem: the model was allowed to make one giant leap instead of a series of small lab steps.

## v11 control loop

Every run now includes a deterministic work protocol:

1. decompose
2. write or patch one small artifact
3. run or inspect one thing
4. repair from stdout/stderr or tool error
5. record durable claims with evidence
6. finalize only when the transcript supports the claim

## Generic safeguards

### 1. Atomic action rule

If a provider emits multiple tool calls in one response, v11 executes only the first one. The next step observes the result and decides the next action.

### 2. Payload limits

Tool calls are checked before execution. Overlarge calls are rejected with an `action_rejected` event and the model is asked to shrink the next step.

This applies to all tasks, not just pi/e digit experiments.

### 3. Parse repair as observation

Parse failures are no longer fatal by default. They are written into the transcript as `parse_repair` observations, and the model gets a compact retry instruction.

### 4. Completion gates

For code/artifact tasks, final answers are not accepted if they claim files or program output without matching tool evidence.

A final answer claiming code was written requires a successful `file_write`.
A final answer claiming results/output requires a successful `python_run`.

### 5. Compact lab notebook

The model no longer receives the entire raw transcript. It receives a compact lab notebook:

- recent tool names
- paths
- return codes
- stdout/stderr snippets
- compact parse/tool failures

Full raw detail still goes into Biopsy.

## Design principle

Do not patch every monster. Build an immune system that notices monsters are too large and cuts them into small specimens.
