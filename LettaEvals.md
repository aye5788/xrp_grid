- ---
title: Core concepts | Letta Docs
description: Core concepts of the Letta evaluation framework including targets, datasets, graders, and extractors.
---

Understanding how Letta Evals works and what makes it different.

**Just want to run an eval?** Skip to [Getting Started](/guides/evals/getting-started/index.md) for a hands-on quickstart.

## Built for Stateful Agents

Letta Evals is a testing framework specifically designed for agents that maintain state. Unlike traditional eval frameworks built for simple input-output models, Letta Evals understands that agents:

- Maintain memory across conversations
- Use tools and external functions
- Evolve their behavior based on interactions
- Have persistent context and state

This means you can test aspects of your agent that other frameworks can’t: memory updates, multi-turn conversations, tool usage patterns, and state evolution over time.

## The Evaluation Flow

Every evaluation follows this flow:

**Dataset → Target (Agent) → Extractor → Grader → Gate → Result**

1. **Dataset**: Your test cases (questions, scenarios, expected outputs)
2. **Target**: The agent being evaluated
3. **Extractor**: Pulls out the relevant information from the agent’s response
4. **Grader**: Scores the extracted information
5. **Gate**: Pass/fail criteria for the overall evaluation
6. **Result**: Metrics, scores, and detailed results

### What You Can Test

With Letta Evals, you can test aspects of agents that traditional frameworks can’t:

- **Memory updates**: Did the agent correctly remember the user’s name?
- **Multi-turn conversations**: Can the agent maintain context across multiple exchanges?
- **Tool usage**: Does the agent call the right tools with the right arguments?
- **State evolution**: How does the agent’s internal state change over time?

**Example: Testing Memory Updates**

```
graders:
  memory_check:
    kind: tool # Deterministic grading
    function: contains # Check if ground_truth in extracted content
    extractor: memory_block # Extract from agent memory (not just response!)
    extractor_config:
      block_label: human # Which memory block to check
```

Dataset:

```
{
  "input": "Please remember that I like bananas.",
  "ground_truth": "bananas"
}
```

This doesn’t just check if the agent responded correctly - it verifies the agent actually stored “bananas” in its memory block. Traditional eval frameworks can’t inspect agent state like this.

## Why Evals Matter

AI agents are complex systems that can behave unpredictably. Without systematic evaluation, you can’t:

- **Know if changes improve or break your agent** - Did that prompt tweak help or hurt?
- **Prevent regressions** - Catch when “fixes” break existing functionality
- **Compare approaches objectively** - Which model works better for your use case?
- **Build confidence before deployment** - Ensure quality before shipping to users
- **Track improvement over time** - Measure progress as you iterate

Manual testing doesn’t scale. Evals let you test hundreds of scenarios in minutes.

## What Evals Are Useful For

### 1. Development & Iteration

- Test prompt changes instantly across your entire test suite
- Experiment with different models and compare results
- Validate that new features work as expected

### 2. Quality Assurance

- Prevent regressions when modifying agent behavior
- Ensure agents handle edge cases correctly
- Verify tool usage and memory updates

### 3. Model Selection

- Compare GPT-4 vs Claude vs other models on your specific use case
- Test different model configurations (temperature, system prompts, etc.)
- Find the right cost/performance tradeoff

### 4. Benchmarking

- Measure agent performance on standard tasks
- Track improvements over time
- Share reproducible results with your team

### 5. Production Readiness

- Validate agents meet quality thresholds before deployment
- Run continuous evaluation in CI/CD pipelines
- Monitor production agent quality

## How Letta Evals Works

Letta Evals is built around a few key concepts that work together to create a flexible evaluation framework.

## Key Components

### Suite

An **evaluation suite** is a complete test configuration defined in a YAML file. It ties together:

- Which dataset to use
- Which agent to test
- How to grade responses
- What criteria determine pass/fail

Think of a suite as a reusable test specification.

### Dataset

A **dataset** is a JSONL file where each line represents one test case. Each sample has:

- An input (what to ask the agent)
- Optional ground truth (the expected answer)
- Optional metadata (tags, custom fields)

### Target

The **target** is what you’re evaluating. Currently, this is a Letta agent, specified by:

- An agent file (.af)
- An existing agent ID
- A Python script that creates agents programmatically

### Trajectory

A **trajectory** is the complete conversation history from one test case. It’s a list of turns, where each turn contains a list of Letta messages (assistant messages, tool calls, tool returns, etc.).

### Extractor

An **extractor** determines what part of the trajectory to evaluate. For example:

- The last thing the agent said
- All tool calls made
- Content from agent memory
- Text matching a pattern

### Grader

A **grader** scores how well the agent performed. There are two types:

- **Tool graders**: Python functions that compare submission to ground truth
- **Rubric graders**: LLM judges that evaluate based on custom criteria

### Gate

A **gate** is the pass/fail threshold for your evaluation. It compares aggregate metrics (like average score or pass rate) against a target value.

## Multi-Metric Evaluation

You can define multiple graders in one suite to evaluate different aspects:

```
graders:
  accuracy: # Check if answer is correct
    kind: tool
    function: exact_match
    extractor: last_assistant # Use final response


  tool_usage: # Check if agent called the right tool
    kind: tool
    function: contains
    extractor: tool_arguments # Extract tool call args
    extractor_config:
      tool_name: search # From search tool
```

The gate can check any of these metrics:

```
gate:
  metric_key: accuracy # Gate on accuracy (tool_usage still computed)
  op: gte # >=
  value: 0.8 # 80% threshold
```

## Score Normalization

All scores are normalized to the range \[0.0, 1.0]:

- 0.0 = complete failure
- 1.0 = perfect success
- Values in between = partial credit

This allows different grader types to be compared and combined.

## Aggregate Metrics

Individual sample scores are aggregated in two ways:

1. **Average Score**: Mean of all scores (0.0 to 1.0)
2. **Accuracy/Pass Rate**: Percentage of samples passing a threshold

You can gate on either metric type.

## Next Steps

Dive deeper into each concept:

- [Suites](/guides/evals/concepts/suites/index.md) - Suite configuration in detail
- [Datasets](/guides/evals/concepts/datasets/index.md) - Creating effective test datasets
- [Targets](/guides/evals/concepts/targets/index.md) - Agent configuration options
- [Graders](/guides/evals/concepts/graders/index.md) - Understanding grader types
- [Extractors](/guides/evals/concepts/extractors/index.md) - Extraction strategies
- [Gates](/guides/evals/concepts/gates/index.md) - Setting pass/fail criteria--
title: Suites | Letta Docs
---

A **suite** is a YAML configuration file that defines a complete evaluation specification. It’s the central piece that ties together your dataset, target agent, grading criteria, and pass/fail thresholds.

**Quick overview:** - **Single file defines everything**: Dataset, agent, graders, and success criteria all in one YAML - **Reusable and shareable**: Version control your evaluation specs alongside your code - **Multi-metric support**: Evaluate multiple aspects (accuracy, quality, tool usage) in one run - **Multi-model testing**: Run the same suite across different LLM models

- **Flexible filtering**: Test subsets using tags or sample limits

**Typical workflow:**

1. Create a suite YAML defining what and how to test
2. Run `letta-evals run suite.yaml`
3. Review results showing scores for each metric
4. Suite passes or fails based on gate criteria

An evaluation suite is a YAML configuration file that defines a complete test specification.

## Basic Structure

```
name: my-evaluation # Suite identifier
description: Optional description of what this tests # Human-readable explanation
dataset: path/to/dataset.jsonl # Test cases


target: # What agent to evaluate
  kind: agent
  agent_file: agent.af # Agent to test
  base_url: https://api.letta.com # Letta server


graders: # How to evaluate responses
  my_metric:
    kind: tool # Deterministic grading
    function: exact_match # Grading function
    extractor: last_assistant # What to extract from agent response


gate: # Pass/fail criteria
  metric_key: my_metric # Which metric to check
  op: gte # Greater than or equal
  value: 0.8 # 80% threshold
```

## Required Fields

### name

The name of your evaluation suite. Used in output and results.

```
name: question-answering-eval
```

### dataset

Path to the JSONL or CSV dataset file. Can be relative (to the suite YAML) or absolute.

```
dataset: ./datasets/qa.jsonl # Relative to suite YAML location
```

### target

Specifies what agent to evaluate. See [Targets](/guides/evals/concepts/targets/index.md) for details.

### graders

One or more graders to evaluate agent performance. See [Graders](/guides/evals/concepts/graders/index.md) for details.

### gate

Pass/fail criteria. See [Gates](/guides/evals/concepts/gates/index.md) for details.

## Optional Fields

### description

A human-readable description of what this suite tests:

```
description: Tests the agent's ability to answer factual questions accurately
```

### max\_samples

Limit the number of samples to evaluate (useful for quick tests):

```
max_samples: 10 # Only evaluate first 10 samples
```

### sample\_tags

Filter samples by tags (only evaluate samples with these tags):

```
sample_tags: [math, easy] # Only samples tagged with "math" AND "easy"
```

Dataset samples can include tags:

```
{
  "input": "What is 2+2?",
  "ground_truth": "4",
  "tags": [
    "math",
    "easy"
  ]
}
```

### num\_runs

Number of times to run the entire evaluation suite (useful for testing non-deterministic behavior):

```
num_runs: 5 # Run the evaluation 5 times
```

Default: 1

### setup\_script

Path to a Python script with a setup function to run before evaluation:

```
setup_script: setup.py:prepare_environment # script.py:function_name
```

The setup function should have this signature:

```
def prepare_environment(suite: SuiteSpec) -> None:
    # Setup code here
    pass
```

## Path Resolution

Paths in the suite YAML are resolved relative to the YAML file location:

```
project/
├── suite.yaml
├── dataset.jsonl
└── agents/
    └── my_agent.af
```

```
# In suite.yaml
dataset: dataset.jsonl # Resolves to project/dataset.jsonl
target:
  agent_file: agents/my_agent.af # Resolves to project/agents/my_agent.af
```

Absolute paths are used as-is.

## Multi-Grader Suites

You can evaluate multiple metrics in one suite:

```
graders:
  accuracy: # Check if answer is correct
    kind: tool
    function: exact_match
    extractor: last_assistant


  completeness: # LLM judges response quality
    kind: rubric
    prompt_path: rubrics/completeness.txt
    model: gpt-4o-mini
    extractor: last_assistant


  tool_usage: # Verify correct tool was called
    kind: tool
    function: contains
    extractor: tool_arguments # Extract tool call arguments
```

The gate can check any of these metrics:

```
gate:
  metric_key: accuracy # Gate on accuracy metric (others still computed)
  op: gte # Greater than or equal
  value: 0.9 # 90% threshold
```

Results will include scores for all graders, even if you only gate on one.

## Examples

### Simple Tool Grader Suite

```
name: basic-qa # Suite name
dataset: questions.jsonl # Test questions


target:
  kind: agent
  agent_file: qa_agent.af # Pre-configured agent
  base_url: https://api.letta.com # Local server


graders:
  accuracy: # Single metric
    kind: tool # Deterministic grading
    function: contains # Check if ground truth is in response
    extractor: last_assistant # Use final agent message


gate:
  metric_key: accuracy # Gate on this metric
  op: gte # Must be >=
  value: 0.75 # 75% to pass
```

### Rubric Grader Suite

```
name: quality-eval # Quality evaluation
dataset: prompts.jsonl # Test prompts


target:
  kind: agent
  agent_id: existing-agent-123 # Use existing agent
  base_url: https://api.letta.com # Letta API


graders:
  quality: # LLM-as-judge metric
    kind: rubric # Subjective evaluation
    prompt_path: quality_rubric.txt # Rubric template
    model: gpt-4o-mini # Judge model
    temperature: 0.0 # Deterministic
    extractor: last_assistant # Evaluate final response


gate:
  metric_key: quality # Gate on this metric
  metric: avg_score # Use average score
  op: gte # Must be >=
  value: 0.7 # 70% to pass
```

### Multi-Model Suite

Test the same agent configuration across different models:

```
name: model-comparison # Compare model performance
dataset: test.jsonl # Same test for all models


target:
  kind: agent
  agent_file: agent.af # Same agent configuration
  base_url: https://api.letta.com # Local server
  model_configs: [gpt-4o-mini, claude-3-5-sonnet] # Test both models


graders:
  accuracy: # Single metric for comparison
    kind: tool
    function: exact_match
    extractor: last_assistant


gate:
  metric_key: accuracy # Both models must pass this
  op: gte # Must be >=
  value: 0.8 # 80% threshold
```

Results will show per-model metrics.

## Validation

Validate your suite configuration before running:

Terminal window

```
letta-evals validate suite.yaml
```

This checks:

- Required fields are present
- Paths exist
- Configuration is valid
- Grader/extractor combinations are compatible

## Next Steps

- [Dataset Configuration](/guides/evals/concepts/datasets/index.md)
- [Target Configuration](/guides/evals/concepts/targets/index.md)
- [Grader Configuration](/guides/evals/concepts/graders/index.md)
- [Gate Configuration](/guides/evals/concepts/gates/index.md)

---
title: Datasets | Letta Docs
description: Create and manage evaluation datasets with test cases for systematic agent testing.
---

**Datasets** are the test cases that define what your agent will be evaluated on. Each sample in your dataset represents one evaluation scenario.

**Quick overview:** - **Two formats**: JSONL (flexible, powerful) or CSV (simple, spreadsheet-friendly) - **Required field**: `input` - the prompt(s) to send to the agent - **Common fields**: `ground_truth` (expected answer), `tags` (for filtering), `metadata` (extra info) - **Advanced fields**: `agent_args` (customize agent per sample), `rubric_vars` (per-sample rubric context) - **Multi-turn support**: Send multiple messages in sequence using arrays

**Typical workflow:**

1. Create a JSONL or CSV file with test cases
2. Reference it in your suite YAML: `dataset: test_cases.jsonl`
3. Run evaluation - each sample is tested independently
4. Results show per-sample and aggregate scores

Datasets can be created in two formats: **JSONL** or **CSV**. Choose based on your team’s workflow and complexity needs.

## Dataset Formats

### JSONL Format

Each line is a JSON object representing one test case:

```
{"input": "What's the capital of France?", "ground_truth": "Paris"}
{"input": "Calculate 2+2", "ground_truth": "4"}
{"input": "What color is the sky?", "ground_truth": "blue"}
```

**Best for:**

- Complex data structures (nested objects, arrays)
- Multi-turn conversations
- Advanced features (agent\_args, rubric\_vars)
- Teams comfortable with JSON/code
- Version control (clean line-by-line diffs)

### CSV Format

Standard CSV with headers:

```
input,ground_truth
"What's the capital of France?","Paris"
"Calculate 2+2","4"
"What color is the sky?","blue"
```

**Best for:**

- Simple question-answer pairs
- Teams that prefer spreadsheets (Excel, Google Sheets)
- Non-technical collaborators creating test cases
- Quick dataset creation and editing
- Easy sharing with non-developers

## Quick Reference

| Field          | Required | Type             | Purpose                              |
| -------------- | -------- | ---------------- | ------------------------------------ |
| `input`        | ✅        | string or array  | Prompt(s) to send to agent           |
| `ground_truth` | ❌        | string           | Expected answer (for tool graders)   |
| `tags`         | ❌        | array of strings | For filtering samples                |
| `agent_args`   | ❌        | object           | Per-sample agent customization       |
| `rubric_vars`  | ❌        | object           | Per-sample rubric variables          |
| `metadata`     | ❌        | object           | Arbitrary extra data                 |
| `id`           | ❌        | integer          | Sample ID (auto-assigned if omitted) |

## Field Reference

### Required Fields

#### input

The prompt(s) to send to the agent. Can be a string or array of strings:

Single message:

```
{ "input": "Hello, who are you?" }
```

Multi-turn conversation:

```
{ "input": ["Hello", "What's your name?", "Tell me about yourself"] }
```

### Optional Fields

#### ground\_truth

The expected answer or content to check against. Required for most tool graders (exact\_match, contains, etc.):

```
{ "input": "What is 2+2?", "ground_truth": "4" }
```

#### metadata

Arbitrary additional data about the sample:

```
{
  "input": "What is photosynthesis?",
  "ground_truth": "process where plants convert light into energy",
  "metadata": {
    "category": "biology",
    "difficulty": "medium"
  }
}
```

#### tags

List of tags for filtering samples:

```
{ "input": "Solve x^2 = 16", "ground_truth": "4", "tags": ["math", "algebra"] }
```

Filter by tags in your suite:

```
sample_tags: [math] # Only samples tagged "math" will be evaluated
```

#### agent\_args

Custom arguments passed to programmatic agent creation when using `agent_script`. Allows per-sample agent customization.

JSONL:

```
{
  "input": "What items do we have?",
  "agent_args": {
    "item": { "sku": "SKU-123", "name": "Widget A", "price": 19.99 }
  }
}
```

CSV:

```
input,agent_args
"What items do we have?","{""item"": {""sku"": ""SKU-123"", ""name"": ""Widget A"", ""price"": 19.99}}"
```

Your agent factory function can access these values via `sample.agent_args` to customize agent configuration.

See [Targets - agent\_script](/guides/evals/concepts/targets#agent_script/index.md) for details on programmatic agent creation.

#### rubric\_vars

Variables to inject into rubric templates when using rubric graders. This allows you to provide per-sample context or examples to the LLM judge.

**Example:** Evaluating code quality against a reference implementation.

JSONL:

```
{
  "input": "Write a function to calculate fibonacci numbers",
  "rubric_vars": {
    "reference_code": "def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)",
    "required_features": "recursion, base case"
  }
}
```

CSV:

```
input,rubric_vars
"Write a function to calculate fibonacci numbers","{""reference_code"": ""def fib(n):\n    if n <= 1: return n\n    return fib(n-1) + fib(n-2)"", ""required_features"": ""recursion, base case""}"
```

In your rubric template file, reference variables with `{variable_name}`:

**rubric.txt:**

```
Evaluate the submitted code against this reference implementation:


{reference_code}


Required features: {required_features}


Score on correctness (0.6) and code quality (0.4).
```

When the rubric grader runs, variables are replaced with values from `rubric_vars`:

**Final formatted prompt sent to LLM:**

```
Evaluate the submitted code against this reference implementation:


def fib(n):
    if n <= 1: return n
    return fib(n-1) + fib(n-2)


Required features: recursion, base case


Score on correctness (0.6) and code quality (0.4).
```

This lets you customize evaluation criteria per sample using the same rubric template.

See [Rubric Graders](/guides/evals/graders/rubric-graders/index.md) for details on rubric templates.

#### id

Sample ID is automatically assigned (0-based index) if not provided. You can override:

```
{ "id": 42, "input": "Test case 42" }
```

## Complete Example

```
{"id": 1, "input": "What is the capital of France?", "ground_truth": "Paris", "tags": ["geography", "easy"], "metadata": {"region": "Europe"}}
{"id": 2, "input": "Calculate the square root of 144", "ground_truth": "12", "tags": ["math", "medium"]}
{"id": 3, "input": ["Hello", "What can you help me with?"], "tags": ["conversation"]}
```

## Dataset Best Practices

### 1. Clear Ground Truth

Make ground truth specific enough to grade but flexible enough to match valid responses:

Good:

```
{"input": "What's the largest planet?", "ground_truth": "Jupiter"}
```

Too strict (might miss valid answers):

```
{"input": "What's the largest planet?", "ground_truth": "Jupiter is the largest planet in our solar system."}
```

### 2. Diverse Test Cases

Include edge cases and variations:

```
{"input": "What is 2+2?", "ground_truth": "4", "tags": ["math", "easy"]}
{"input": "What is 0.1 + 0.2?", "ground_truth": "0.3", "tags": ["math", "floating_point"]}
{"input": "What is 999999999 + 1?", "ground_truth": "1000000000", "tags": ["math", "large_numbers"]}
```

### 3. Use Tags for Organization

Organize samples by type, difficulty, or feature:

```
{"tags": ["tool_usage", "search"]}
{"tags": ["memory", "recall"]}
{"tags": ["reasoning", "multi_step"]}
```

### 4. Multi-Turn Conversations

Test conversational context and memory updates:

```
{"input": ["My name is Alice", "What's my name?"], "ground_truth": "Alice", "tags": ["memory", "recall"]}
{"input": ["Please remember that I like bananas.", "Actually, sorry, I meant I like apples."], "ground_truth": "apples", "tags": ["memory", "correction"]}
{"input": ["I work at Google", "Update my workplace to Microsoft", "Where do I work?"], "ground_truth": "Microsoft", "tags": ["memory", "multi_step"]}
```

**Testing memory corrections:** Use multi-turn inputs to test if agents properly update memory when users correct themselves. Combine with the `memory_block` extractor to verify the final memory state, not just the response.

### 5. No Ground Truth for LLM Judges

If using rubric graders, ground truth is optional:

```
{"input": "Write a creative story about a robot", "tags": ["creative"]}
{"input": "Explain quantum computing simply", "tags": ["explanation"]}
```

The LLM judge evaluates based on the rubric, not ground truth.

## Loading Datasets

Datasets are automatically loaded by the runner:

```
dataset: path/to/dataset.jsonl # Path to your test cases (JSONL or CSV)
```

Paths are relative to the suite YAML file location.

## Dataset Filtering

### Limit Sample Count

```
max_samples: 10 # Only evaluate first 10 samples (useful for testing)
```

### Filter by Tags

```
sample_tags: [math, medium] # Only samples with ALL these tags
```

## Creating Datasets Programmatically

You can generate datasets with Python:

```
import json


samples = []
for i in range(100):
    samples.append({
        "input": f"What is {i} + {i}?",
        "ground_truth": str(i + i),
        "tags": ["math", "addition"]
    })


with open("dataset.jsonl", "w") as f:
    for sample in samples:
        f.write(json.dumps(sample) + "\n")
```

## Dataset Format Validation

The runner validates:

- Each line is valid JSON
- Required fields are present
- Field types are correct

Validation errors will be reported with line numbers.

## Examples by Use Case

### Question Answering

JSONL:

```
{"input": "What is the capital of France?", "ground_truth": "Paris"}
{"input": "Who wrote Romeo and Juliet?", "ground_truth": "Shakespeare"}
```

CSV:

```
input,ground_truth
"What is the capital of France?","Paris"
"Who wrote Romeo and Juliet?","Shakespeare"
```

### Tool Usage Testing

JSONL:

```
{"input": "Search for information about pandas", "ground_truth": "search"}
{"input": "Calculate 15 * 23", "ground_truth": "calculator"}
```

CSV:

```
input,ground_truth
"Search for information about pandas","search"
"Calculate 15 * 23","calculator"
```

Ground truth = expected tool name.

### Memory Testing (Multi-turn)

JSONL:

```
{"input": ["Remember that my favorite color is blue", "What's my favorite color?"], "ground_truth": "blue"}
{"input": ["I live in Tokyo", "Where do I live?"], "ground_truth": "Tokyo"}
```

CSV (using JSON array strings):

```
input,ground_truth
"[""Remember that my favorite color is blue"", ""What's my favorite color?""]","blue"
"[""I live in Tokyo"", ""Where do I live?""]","Tokyo"
```

### Code Generation

JSONL:

```
{"input": "Write a function to reverse a string in Python"}
{"input": "Create a SQL query to find users older than 21"}
```

CSV:

```
input
"Write a function to reverse a string in Python"
"Create a SQL query to find users older than 21"
```

Use rubric graders to evaluate code quality.

## CSV Advanced Features

CSV supports all the same features as JSONL by encoding complex data as JSON strings in cells:

**Multi-turn conversations** (requires escaped JSON array string):

```
input,ground_truth
"[""Hello"", ""What's your name?""]","Alice"
```

**Agent arguments** (requires escaped JSON object string):

```
input,agent_args
"What items do we have?","{""initial_inventory"": [""apple"", ""banana""]}"
```

**Rubric variables** (requires escaped JSON object string):

```
input,rubric_vars
"Write a story","{""max_length"": 500, ""genre"": ""sci-fi""}"
```

**Note:** Complex data structures require JSON encoding in CSV. If you’re frequently using these advanced features, JSONL may be easier to read and maintain.

## Next Steps

- [Suite YAML Reference](/guides/evals/configuration/suite-yaml/index.md) - Complete configuration options including filtering
- [Graders](/guides/evals/concepts/graders/index.md) - How to evaluate agent responses
- [Multi-Turn Conversations](/guides/evals/advanced/multi-turn-conversations/index.md) - Testing conversational flows

---
title: Targets | Letta Docs
description: Define evaluation targets that specify which agents or systems to test in your evaluation runs.
---

A **target** is the agent you’re evaluating. In Letta Evals, the target configuration determines how agents are created, accessed, and tested.

**Quick overview:** - **Three ways to specify agents**: agent file (`.af`), existing agent ID, or programmatic creation script - **Critical distinction**: `agent_file`/`agent_script` create fresh agents per sample (isolated tests), while `agent_id` uses one agent for all samples (stateful conversation) - **Multi-model support**: Test the same agent configuration across different LLM models - **Flexible connection**: Connect to local Letta servers or Letta Cloud

**When to use each approach:**

- `agent_file` - Pre-configured agents saved as `.af` files (most common)
- `agent_id` - Testing existing agents or multi-turn conversations with state
- `agent_script` - Dynamic agent creation with per-sample customization

The target configuration specifies how to create or access the agent for evaluation.

## Target Configuration

All targets have a `kind` field (currently only `agent` is supported):

```
target:
  kind: agent # Currently only "agent" is supported
  # ... agent-specific configuration
```

## Agent Sources

You must specify exactly ONE of these:

### agent\_file

Path to a `.af` (Agent File) to upload:

```
target:
  kind: agent
  agent_file: path/to/agent.af # Path to .af file
  base_url: https://api.letta.com # Letta server URL
```

The agent file will be uploaded to the Letta server and a new agent created for the evaluation.

### agent\_id

ID of an existing agent on the server:

```
target:
  kind: agent
  agent_id: agent-123-abc # ID of existing agent
  base_url: https://api.letta.com # Letta server URL
```

**Modifies agent in-place:** Using `agent_id` will modify your agent’s state, memory, and message history during evaluation. The same agent instance is used for all samples, processing them sequentially. **Do not use production agents or agents you don’t want to modify.** Use `agent_file` or `agent_script` for reproducible, isolated testing.

### agent\_script

Path to a Python script with an agent factory function for programmatic agent creation:

```
target:
  kind: agent
  agent_script: create_agent.py:create_inventory_agent # script.py:function_name
  base_url: https://api.letta.com # Letta server URL
```

Format: `path/to/script.py:function_name`

The function must be decorated with `@agent_factory` and have the signature `async (client: AsyncLetta, sample: Sample) -> str`:

```
from letta_client import AsyncLetta, CreateBlock
from letta_evals.decorators import agent_factory
from letta_evals.models import Sample


@agent_factory
async def create_inventory_agent(client: AsyncLetta, sample: Sample) -> str:
    """Create and return agent ID for this sample."""
    # Access custom arguments from the dataset
    item = sample.agent_args.get("item", {})


    # Create agent with sample-specific configuration
    agent = await client.agents.create(
        name="inventory-assistant",
        memory_blocks=[
            CreateBlock(
                label="item_context",
                value=f"Item: {item.get('name', 'Unknown')}"
            )
        ],
        agent_type="letta_v1_agent",
        model="openai/gpt-4.1-mini",
        embedding="openai/text-embedding-3-small",
    )


    return agent.id
```

**Key features:**

- Creates a fresh agent for each sample
- Can customize agents using `sample.agent_args` from the dataset
- Allows testing agent creation logic itself
- Useful when you don’t have pre-saved agent files

**When to use:**

- Testing agent creation workflows
- Dynamic per-sample agent configuration
- Agents that need sample-specific memory or tools
- Programmatic agent testing

## Connection Configuration

### base\_url

Letta server URL:

```
target:
  base_url: https://api.letta.com  # Local Letta server
  # or
  base_url: https://api.letta.com  # Letta API
```

Default: `https://api.letta.com`

### api\_key

API key for authentication (required for Letta API):

```
target:
  api_key: your-api-key-here # Required for Letta API
```

Or set via environment variable:

Terminal window

```
export LETTA_API_KEY=your-api-key-here
```

### project\_id

Letta project ID (for Letta API):

```
target:
  project_id: proj_abc123 # Letta API project
```

Or set via environment variable:

Terminal window

```
export LETTA_PROJECT_ID=proj_abc123
```

### timeout

Request timeout in seconds:

```
target:
  timeout: 300.0 # Request timeout (5 minutes)
```

Default: 300 seconds

## Multi-Model Evaluation

Test the same agent across different models:

### model\_configs

List of model configuration names from JSON files:

```
target:
  kind: agent
  agent_file: agent.af
  model_configs: [gpt-4o-mini, claude-3-5-sonnet] # Test with both models
```

The evaluation will run once for each model config. Model configs are JSON files in `letta_evals/llm_model_configs/`.

### model\_handles

List of model handles (cloud-compatible identifiers):

```
target:
  kind: agent
  agent_file: agent.af
  model_handles: ["openai/gpt-4o-mini", "anthropic/claude-3-5-sonnet"] # Cloud model identifiers
```

Use this for Letta API deployments.

**Note**: You cannot specify both `model_configs` and `model_handles`.

## Complete Examples

### Local Development

```
target:
  kind: agent
  agent_file: ./agents/my_agent.af # Pre-configured agent
  base_url: https://api.letta.com # Local server
```

### Letta API

```
target:
  kind: agent
  agent_id: agent-cloud-123 # Existing cloud agent
  base_url: https://api.letta.com # Letta API
  api_key: ${LETTA_API_KEY} # From environment variable
  project_id: proj_abc # Your project ID
```

### Multi-Model Testing

```
target:
  kind: agent
  agent_file: agent.af # Same agent configuration
  base_url: https://api.letta.com # Local server
  model_configs: [gpt-4o-mini, gpt-4o, claude-3-5-sonnet] # Test 3 models
```

Results will include per-model metrics:

```
Model: gpt-4o-mini    - Avg: 0.85, Pass: 85.0%
Model: gpt-4o         - Avg: 0.92, Pass: 92.0%
Model: claude-3-5-sonnet - Avg: 0.88, Pass: 88.0%
```

### Programmatic Agent Creation

```
target:
  kind: agent
  agent_script: setup.py:CustomAgentFactory # Programmatic creation
  base_url: https://api.letta.com # Local server
```

## Environment Variable Precedence

Configuration values are resolved in this order (highest priority first):

1. CLI arguments (`--api-key`, `--base-url`, `--project-id`)
2. Suite YAML configuration
3. Environment variables (`LETTA_API_KEY`, `LETTA_BASE_URL`, `LETTA_PROJECT_ID`)

## Agent Lifecycle and Testing Behavior

The way your agent is specified fundamentally changes how the evaluation runs:

### With agent\_file or agent\_script: Independent Testing

**Agent lifecycle:**

1. A fresh agent instance is created for each sample
2. Agent processes the sample input(s)
3. Agent remains on the server after the sample completes

**Testing behavior:** Each sample is an independent, isolated test. Agent state (memory, message history) does not carry over between samples. This enables parallel execution and ensures reproducible results.

**Use cases:**

- Testing how the agent responds to various independent inputs
- Ensuring consistent behavior across different scenarios
- Regression testing where each case should be isolated
- Evaluating agent responses without prior context

**Example:** If you have 10 test cases, 10 separate agent instances will be created (one per test case), and they can run in parallel.

### With agent\_id: Sequential Script Testing

**Agent lifecycle:**

1. The same agent instance is used for all samples
2. Agent processes each sample in sequence
3. Agent state persists throughout the entire evaluation

**Testing behavior:** The dataset becomes a conversation script where each sample builds on previous ones. Agent memory and message history accumulate, and earlier interactions affect later responses. Samples must execute sequentially.

**Use cases:**

- Testing multi-turn conversations with context
- Evaluating how agent memory evolves over time
- Simulating a single user session with multiple interactions
- Testing scenarios where context should accumulate

**Example:** If you have 10 test cases, they all run against the same agent instance in order, with state carrying over between each test.

### Critical Differences

| Aspect              | agent\_file / agent\_script | agent\_id                  |
| ------------------- | --------------------------- | -------------------------- |
| **Agent instances** | New agent per sample        | Same agent for all samples |
| **State isolation** | Fully isolated              | State carries over         |
| **Execution**       | Can run in parallel         | Must run sequentially      |
| **Memory**          | Fresh for each sample       | Accumulates across samples |
| **Use case**        | Independent test cases      | Conversation scripts       |
| **Reproducibility** | Highly reproducible         | Depends on execution order |

**Best practice:** Use `agent_file` or `agent_script` for most evaluations to ensure reproducible, isolated tests. Use `agent_id` only when you specifically need to test how agent state evolves across multiple interactions.

## Validation

The runner validates:

- Exactly one of `agent_file`, `agent_id`, or `agent_script` is specified
- Agent files have `.af` extension
- Agent script paths are valid

## Next Steps

- [Suite YAML Reference](/guides/evals/configuration/suite-yaml/index.md) - Complete target configuration options
- [Datasets](/guides/evals/concepts/datasets/index.md) - Using agent\_args for sample-specific configuration
- [Getting Started](/guides/evals/getting-started/index.md) - Complete tutorial with target examples

