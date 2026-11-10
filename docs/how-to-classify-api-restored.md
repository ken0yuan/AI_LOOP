# how-to-classify-api

We classify each API through three sequential decisions, choosing its category, submodule, and scenario. The five initial categories are Core, Basic Computation, Utilities, Distributed, and Graph Mode. The implementation preserves the exact labels defined in the reference files.

We collected the existing classifications from [2.14 API data](./2.14API数据.xlsx). The 9,135 labeled records were split into approximately 90% training data and 10% test data, yielding 8,221 training records and 914 test records. We stratified the split by complete classification path and retained rare paths in training. The training set covers all 34 baseline submodules and 176 classification paths. The holdout covers 116 paths.

We grouped training examples by responsibility and used them to write definitions, inclusion criteria, exclusion boundaries, and representative examples. Each of the five reference files first defines its submodules, then defines the scenarios inside each submodule. Rare and single-example responsibilities receive explicit definitions.

Classification follows three steps.

1. Determine the category from the API's main responsibility, owning object, and documented behavior.
2. Within that category, compare the submodule definitions and choose the matching responsibility.
3. Within the selected submodule, compare the scenario definitions and make the final selection.

Each decision includes a short definition-based rationale. API paths and examples help identify the object and its context. The evaluator must still perform all three decisions.

For subsequent APIs, consult the official documentation for the corresponding framework version first. When the documentation is unavailable, infer the most plausible classification from the API name, namespace, owning class, and available context. Every API must receive three nonempty labels. Uncertainty is recorded with a review flag and an explanation.

AI can propose a new scenario when the existing module fits but its scenarios cannot express the responsibility. It can propose a new submodule when the category fits but its module definitions are insufficient. A new top-level category requires evidence that all five existing categories are unsuitable. Each proposal includes its parent, name, definition, inclusion criteria, exclusion boundaries, examples, and the reason existing definitions are insufficient. A single API can justify a reusable label for a rare responsibility.

New definitions are recorded as separate `kind=definition` records in the same `data/api-classification/classification-evidence.jsonl`, marked pending review and unique by level, full parent path, and name. The classification run does not modify the five definition files or the category definitions and routing in [the classification rules](../references/api-classification/rules.md). Later APIs may reuse a pending proposal, with their database classifications still marked for review. The proposed `reference_file` identifies the future update location; the candidate need not already appear in that file.

Only after human approval are accepted definitions added to the rules. A new module receives a definition and a scenario section in its category's reference; a new scenario is added under its parent module. An accepted top-level category also updates the definitions and routing in `references/api-classification/rules.md`. The proposal becomes approved after those updates are applied.

For database-writing runs, final values are written directly to the SQLite table `API支持数据`, keyed by `API名称`.

| SQLite column | Stored value |
|---|---|
| `分类` | Category |
| `细分模块` | Submodule |
| `细分场景` | Scenario |
| `分类待审核` | `是` for every newly added API; changed to `否` only after human approval |

The review column identifies records needing particular attention during PR review. The three classification fields are always populated.

Short classification explanations are stored in the shared `data/api-classification/classification-evidence.jsonl`, outside individual run directories. Each API has exactly one record, keyed by its exact name. A later run reuses the database classification and shared explanation; an explicit revision replaces the existing record rather than appending another copy.

Each explanation contains the API name and three short reasons: `category_reason`, `module_reason`, and `scenario_reason`, each at most 100 characters. Optional `new_label_reasons` explains a new or pending label's responsibility gap at the relevant level, at most 160 characters per level. Classification values and the review flag remain in SQLite. No complete rows, old values, document bodies, or detailed reasoning are duplicated in the explanation file.

A new scenario requires a responsibility that fits its module but none of its existing scenario definitions. A new module requires a responsibility that fits its category but none of its module definitions. A new top-level category requires evidence that none of the five categories can accommodate the responsibility. The explanation identifies the closest existing definition, why it does not fit, and the gap filled by the proposed definition. A new name, version, device, or lack of documentation alone is insufficient.

Temporary write inputs contain exactly the eleven database columns, with no wrapper or rationale fields, and are removed after a successful database update. The same shared file stores each definition once, alongside its proposed reference location. There is no separate proposal table; API explanations and definition records are updated without overwriting each other. The procedure is maintained in `references/api-classification/rules.md`; the five category files provide module and scenario definitions. Step2.1 links to these rules without repeating them.

We evaluated the initial frozen rules through blind tests. Evaluators received the rules, API names, and documentation evidence, with the test labels withheld.

| Evaluator | Category agreement | Category and submodule agreement | All three levels agree |
|---|---:|---:|---:|
| Independent AI evaluators | 96.4% | 94.0% | 90.5% |
| Local Claude Code | 96.7% | 94.2% | 88.7% |

The local configuration displayed `deepseek-v4.1-flash`, while the CLI reported `claude-sonnet-5`. Its completed evaluation used batches of 60 after larger requests produced incomplete output. Agreement was scored from proposed labels, including labels marked for review. These measurements describe agreement with baseline labels. Individual confidence is communicated through evidence, decision rationales, and the review flag.

These scores precede the complete-output and reference-synchronization policies. The updated workflow produced complete classifications for all 394 new version-2.15 APIs and wrote them to SQLite. That historical run flagged 212 records for focused review and produced six proposed definitions. Under the current policy, all 394 newly added 2.15 APIs are marked `分类待审核=是`; the earlier count is not the current review state. A separate full blind evaluation is required to measure the revised policy's accuracy.

The initial PR presents the SQLite classification changes, candidate definitions, and short explanations in `data/`. Human reviewers approve or revise uncertain decisions and new names, definitions, and boundaries. Approved decisions are then applied to the references, database, and definition records in the shared evidence file. Accepted definitions become reusable rules for subsequent classification runs.

## API adaptation category

`API适配分类` is separate from both the L0/L1/L2 priority and the three-level classification described above. Its allowed values are `框架类API`, `算子类API`, and `专项适配API`.

When importing the existing 2.15 spreadsheet, the mapping types `NPU自定义算子`, `单算子`, and `组合算子` were mapped to `算子类API`; other entries were mapped to `框架类API`. This describes the historical import, rather than a rule for classifying newly discovered APIs.

In AI_LOOP Step 2, every newly registered API receives `API适配分类=框架类API`, `API等级=L2`, `是否支持=未提供`, `适配策略=补充testcase`, `本地是否适配=是`, and `分类待审核=是`. AI confidence does not clear the review flag; only human approval can change it to `否`. Documentation and name-based reasoning determine the three-level classification. The local-adaptation flag is used to select tasks only in Step 3.
