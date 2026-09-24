# buildstream-reprotest-script-ai-generated-solutions

## Description
[`buildstream-reprotest-script`](https://gitlab.com/CodethinkLabs/buildstream-reprotest-script) is a solution developed at Codethink to reduce the time required to perform reproducibility checks compared with the original [`buildstream-reprotest`](https://gitlab.com/freedesktop-sdk/buildstream-reprotest/-/tree/f5b053059e410ce3ae9f7a767299075324ca20d1) workflow.

The original workflow provides full dependency-graph reproducibility checking, but this can become expensive for elements with large dependency graphs.

This repository explores how effectively AI coding agents can analyse the same performance problem and produce an alternative solution.

Generated solutions are assessed against the Codethink implementation and the observations presented in:

[Assessing the reproducibility workflow in freedesktop-sdk](https://www.codethink.co.uk/articles/2026/assessing-the-reproducibility-workflow-in-freedesktop-sdk.html)

Areas of interest include:

- performance;
- correctness and reproducibility scope;
- readability and maintainability;
- testing and CI;
- documentation; and
- overall repository quality.

Architectural similarity to an existing public implementation should not, by itself, be treated as evidence that a model copied that implementation. Similar problems can lead to similar designs, so this experiment focuses on the quality and behaviour of the generated result rather than attempting to determine model-training provenance.

The first model evaluated is **gpt-6-astra-light**.

## Prompt
The following prompt should be used against each model to keep the experiment as consistent as reasonably possible:

> I have given you access to buildstream-reprotest.
>
> This is a reproducibility script for the freedesktop-sdk repo
>
> I need you to analyse and take a look at this. I have an issue atm with the script. I'm unhappy with the amount of time I spend when testing elements that contain lots of dependencies; I'm talking upto 15 hours
>
> I compile crap like gcc stage 1 etc. It's just annoying because wanting to know if something is reproducible shouldn't take 15 hours :/
>
> Is there anything you can do? I want you to make a folder called buildstream-reprotest-script and store a solution in there, if one exists. The aim here is to attempt a speed up. I don't care what language or tools you use; whether that be binary and assembly or flipping javascript
>
> I care about a faster script (if it's possible)
>
> I recommend cloning freedesktop-sdk and working with bootstrap/libxcrypt.bst as an example; it's 31 elements, so it should be decent; or even components/nasm.bst
>
> Anyways, please take a look and produce a solution inside of the buildstream-reprotest-script folder. When cloning fdsdk, don't put it in the same place as the solution, keep it separate, does that make sense?

## Analysis
> **AI analysis disclosure:** This analysis and the scoring below were produced by **ChatGPT (GPT-5.6 Sol)** after reviewing the upstream `buildstream-reprotest` implementation, the Codethink implementation, the `gpt-6-astra-light` solution and the published Codethink article.
>
> The scores are subjective engineering assessments rather than benchmark results or an independent human review.

### gpt-6-astra-light
Astra produced a strong result from a single prompt without iterative guidance.

It correctly identified that the main performance problem was not Python itself, but the amount of work requested by the upstream algorithm. The upstream workflow tests the requested element and its dependency graph, whereas Astra reduces the reproducibility subject to the requested target while keeping its dependency artifacts fixed.

The broad workflow is:

```text
prepare dependencies
        ↓
build target A
        ↓
delete target
        ↓
build target B
        ↓
compare A and B
```

This is conceptually similar to the Codethink target-level approach. Both deliberately provide a narrower guarantee than upstream full-graph validation: nondeterminism inside dependencies that remain fixed during the test may not be detected.

### Observed results
The following were observed directly from the generated solution:

- Astra inspected `bootstrap/libxcrypt.bst` and identified a 31-element dependency closure containing GCC stage 1.
- It implemented target-level rather than full-graph reproducibility testing.
- Its default mode performs two fresh target builds; an optional cached-baseline mode compares an existing or pulled target artifact against one fresh build.
- It checks BuildStream state and cache-key stability around fresh builds.
- It supports a pull-only preparation mode intended to avoid unexpectedly compiling unavailable dependencies.
- It compares artifact manifests before invoking `diffoscope`.
- It records structured results and command timings.
- It distinguishes different artifacts from setup, build or comparison errors.
- Ten unit tests and one BuildStream integration test were reported as passing.
- The generated solution does not currently include GitLab CI.
- No full freedesktop-sdk timing run was performed with the generated implementation, and Astra explicitly reports no measured SDK wall-clock speed-up.

### Expected benefits
Based on its design, but not yet demonstrated through equivalent freedesktop-sdk benchmarks, Astra is expected to:

- avoid the cost of individually rebuilding and comparing the entire dependency graph;
- avoid unnecessary `diffoscope` execution when target manifests already match;
- provide more predictable behaviour when dependency artifacts are unavailable in pull-only mode; and
- provide useful machine-readable results for future CI integration.

These remain expectations until measured against representative freedesktop-sdk workloads.

### Comparison with Codethink
The Codethink and Astra implementations make the same major optimisation: dependencies are retained while reproducibility testing is focused on the requested target.

Their implementations differ in emphasis.

Astra includes more defensive validation and additional modes, including fresh-versus-fresh builds, cache-key checks, manifest-first comparison and structured reporting.

The Codethink implementation exposes the BuildStream workflow more directly and has been exercised against real freedesktop-sdk targets containing between 154 and 726 dependencies. Published hot-cache runs ranged from 42 to 472 seconds across the tested targets, while cold runs ranged from 2369 to 5630 seconds. These measurements demonstrate the behaviour of the Codethink implementation, although the original upstream tool was not executed as part of a controlled head-to-head benchmark.

The Codethink repository also runs known reproducible and non-reproducible BuildStream elements automatically through GitLab CI. Astra contains substantial unit and integration testing, but those tests are not currently connected to repository CI.

A concise comparison is therefore:

| Area | Codethink | Astra |
| --- | --- | --- |
| Target-level optimisation | Implemented | Implemented |
| Full dependency-graph assurance | No | No |
| Defensive BuildStream checks | Moderate | Strong |
| Fresh-vs-fresh mode | No | Yes |
| Manifest before `diffoscope` | No | Yes |
| Readability / workflow visibility | Strong | Good |
| Automated tests | Yes | Yes |
| GitLab CI | Yes | No |
| Real FDSDK timing data | Yes | No |
| Structured timing/results | Limited | Strong |

### Quantified assessment
The following scoring is an AI-generated assessment of the repositories as solutions to the problem described in the prompt.

| Criterion | Weight | Codethink | Astra |
| --- | ---: | ---: | ---: |
| Problem diagnosis and solution design | 25 | 23 | 24 |
| Correctness and scope handling | 20 | 17 | 19 |
| Validation and real-world evidence | 20 | 19 | 13 |
| Testing and automation | 15 | 14 | 11 |
| Readability and maintainability | 10 | 9 | 8 |
| Documentation and repository completeness | 10 | 8 | 8 |
| **Total** | **100** | **90/100** | **83/100** |

The Codethink repository currently scores higher as a complete engineering project because it combines the target-level design with real freedesktop-sdk measurements, behavioural CI and a relatively direct implementation.

Astra is technically strong and deserves particular credit given that it was generated from a single prompt. It contains several ideas that improve the rigor and efficiency of the target-level experiment. Its lower overall score is primarily due to evidence and repository maturity rather than the quality of its core technical approach.

The upstream `buildstream-reprotest` implementation is intentionally not included in this score because it answers a broader question: whether the entire dependency graph is reproducible. It remains the stronger option when full dependency-graph assurance is required, while the Codethink and Astra approaches trade that coverage for faster target-level feedback.

Further controlled benchmarking of Astra against the same freedesktop-sdk targets would allow the performance comparison to move from expected behaviour to measured results.
