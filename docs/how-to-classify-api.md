# how-to-classify-api

We use three levels of API classification: category, submodule, and scenario. Each API passes through three sequential decisions, using the definitions at each level.

First, we gathered the classification for APIs from [2.14 API data](./2.14API数据.xlsx). The 9,135 labeled APIs were divided into approximately 90% training data and 10% testing data, producing 8,221 training records and 914 test records.

The split was stratified by the complete classification path. The training set covers all 34 submodules and 176 classification paths. Rare paths, including those represented by a single API, were retained in training. The test set covers 116 classification paths.

We used the training examples to construct five reference files, corresponding to **Core, Basic Computation, Utilities, Distributed, and Graph Mode**. For each category, we defined its responsibilities and boundaries. Within each category, we defined every submodule. Within each submodule, we described every scenario, including its purpose, selection criteria, boundaries, and representative training examples.

Classification follows three steps:

1. **Choose the category.** Identify the API’s main purpose from its documentation and owning object.
2. **Choose the submodule.** Compare that purpose against the submodule definitions within the selected category.
3. **Choose the scenario.** Compare the API’s specific responsibility against the scenario definitions within the selected submodule.

For subsequent APIs, we first consult the official documentation for the corresponding framework version. When documentation is unavailable, we infer the most plausible classification from the API name, namespace, owning class, and surrounding context. Each record includes three short decision rationales and its evidence sources.

The revised policy requires **all three classification fields to be populated**. Ambiguous or weakly supported decisions receive a `needs_review` flag alongside a complete candidate classification.

AI can propose a new scenario when the existing module fits but its scenarios cannot express the responsibility. It can propose a new submodule when the category fits but its module definitions are insufficient. A new top-level category requires evidence that all five existing categories are unsuitable. Each proposal includes its parent, name, definition, inclusion criteria, exclusion boundaries, examples, and the reason existing definitions are insufficient. A single API can justify a reusable label for a rare responsibility.

Every new label updates the appropriate rule definitions during the same run. A new module receives a definition and a scenario section in its category's reference. A new scenario is added under its parent module. A proposed top-level category also updates the main skill's definitions and routing. New definitions are marked pending review. Later APIs can reuse them, and adopting a pending definition or parent carries the review flag into their results. Human approval updates the definition's status.

Decision rationales, documentation evidence, review reasons, and label proposals are intermediate artifacts stored outside the skill directory.

```text
data/api-classification/<run-id>/
  classification-evidence.jsonl
  input-documents.jsonl
  new-label-proposals.json
  manifest.json
```

We evaluated the initial frozen rules through blind tests. Evaluators received the rules, API names, and documentation evidence, with the test labels withheld.

| Evaluator | Category agreement | Category + submodule agreement | All three levels agree |
|---|---:|---:|---:|
| Independent AI evaluators | 96.4% | 94.0% | 90.5% |
| Local Claude Code | 96.7% | 94.2% | 88.7% |

The local Claude Code configuration displayed `deepseek-v4.1-flash`. These percentages measure exact agreement with baseline labels, including proposed labels marked `needs_review`. Individual confidence is represented by the evidence, decision rationales, and review flag.

These scores precede the complete-output and new-label policy. The revised policy produced complete candidates for all 394 new APIs from version 2.15. This validates output completeness; measuring its accuracy requires independently approved three-level labels.

Each PR includes the SQLite classification changes, matching reference changes, and the evidence artifacts in `data/`. Human reviewers approve or revise uncertain decisions and new names, definitions, and boundaries. Review decisions are then synchronized across the database, references, and proposal registry. Accepted definitions become reusable guidance for subsequent classification runs.
