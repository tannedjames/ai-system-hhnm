# RAG Evaluation

Use `evaluation_template.csv` for a 20-30 question test set.

Metrics:
- `hit_at_k`: `1` if any expected source/page appears in retrieved sources.
- `rank`: first position of the relevant source, blank if missing.
- `mrr`: `1 / rank`, or `0` when missing.
- `citation_relevance`: manual score from `0` to `2`.
- `answer_grounded`: `1` if the answer is supported by cited chunks, else `0`.

Suggested demo thresholds:
- Hit@5 >= 0.80
- MRR >= 0.50
- Citation relevance average >= 1.50 / 2
- No unsupported answer for questions outside the corpus

Recommended question mix:
- 10 direct fact questions from the uploaded documents
- 10 comparison/synthesis questions across sections
- 5 negative questions whose answer is not in the corpus
