# Description from the paperqa paper.

8.1 PaperQA Implementation and Parameters
All reported figures and data in this work were built on the open source PaperQA package, available on GitHub at
paperqa. While the core PaperQA repository provides the basic algorithms used, it does not include the Grobid parsing
code, access to non-local full-text literature searches, or the citation traversal tool. The open source version of PaperQA
utilizes LangChain1
for its agentic and state update operations. The full configuration objects for all experiments run in
this paper are included for further customization.
Note that while paperqa gives the ability to recreate this work, the experiments reported in this paper were performed
using a more featureful HTTP server that takes advantage of bespoke infrastructure at the authors’ institution. This
infrastructure includes features such as user authentication, MongoDB request caching, Redis object caching and
global-load balancing, several PostgreSQL DBs with associated ORM code, cost-monitoring modules, time-profiling
modules, configuration storage and run orchestration (Dagster 2
and kubernetes 3
), cloud bucket storage for PDFs, a CI
pipeline with semi-automated deployments, and infrastructure code for deploying auto-scaling instances in the cloud.
None of these features affect performance on a per-query basis, but provide increased scalability, measurability, and
persistence. To run our same server infrastructure, users would need to provision all of these assets and configure the
deployments themselves. paperqa should serve allow usage and customization sufficient for most research purposes,
and should be sufficient to reproduce the results reported here.
Even within paperqa, the “Paper Search” tool is limited by access to full text repositories of scientific papers, often
bound by licensing agreements. The included implementation only works from local files accessible to each user. Our
implementation starts with a full or partial-text keyword search, where the keywords have been specified by the agent
when selecting the paper search tool. The ranked results returned from these services are then matched to a user’s
existing paper repository or can be retrieved on-the-fly if open-access or partner links exist for these works. These
matching papers are parsed, and pulled into our agent state for usage with other tools. Note that the search services will
have access to a larger corpus of works than is available to us via our repository and accessible link traversal, in these
cases the system will simply skip these papers and they are not used. A stub of the paper search tool is implemented
in paperqa with directions for users to implement their own retrieval since it will be limited to their own access to
full-text papers.
Ablations and configurations for workflows like WikiCrow are exposed in paperqa as nested configuration objects. All
experiments performed in this work correspond to included configuration objects. Here we highlight the configuration
variable descriptions corresponding to the salient features tested in this work, though all variable names are available in
the included files.
• query: The main query task asked of the PaperQA agent, i.e. a LitQA question or a directive to write an
article.
• llm: The LLM used in the generate answer tool can be a valid Anthropic, OpenAI, or Gemini model identifier.
This parameter was varied for the model experiments in Figure 2.
• agent_llm: The LLM used for the agent orchestration, in this work, it was always fixed to
gpt-4-turbo-2024-04-09.
• summary_llm: The LLM used for the RCS step in the gather evidence tool, must be a valid Anthropic,
OpenAI, or Gemini model identifier. This parameter was varied for the model ablations in Figure 2.
• prompts: A PromptCollection object from PaperQA4
, which allows for specification of prompts in each
tool, as well as features like turning off RCS (via prompts.skip_summarization). The No RCS Model
ablation used this input as well as the WikiCrow prompts.
12
Language Agents Achieve Superhuman Synthesis of Scientific Knowledge
• max_sources: The number of top ranked sources to be included in the generate answer tool, in Figure 1A,
the ‘filter top summaries” cutoff. This parameter was 5 in the top-performing Answer cutoff @ 5, but 15 for all
other experiments.
• consider_sources: The top-k cutoff, i.e. the number of chunks that will be used in the RCS step. This
parameter was set to 30 by default in LitQA experiments, save for the Top-k Rank @ X experiment where it
was set to X. Additionally, for our WikiCrow prompts this parameter was set to 25.
• agent_tools: An ordered list of tool names that will be used by the agent, including gather_evidence,
paper_search, generate_answer, and citations_traversal. This always included all four tools except
for the No Cit. Trav. and No Agent runs where citations_traversal was excluded.
• docs_index_mmr_lambda: A pre-gather evidence MMR lambda parameter which can be used to pre-filter
similar papers by name before gathering evidence. This was set to 0.9 for our WikiCrow run to promote
diversity of sources, but 1.0 for LitQA experiments.
• parsing_configuration.ordered_parser_preferences: A list of the parsing algorithm to use, either
paperqa_default (PyMuPDF) or grobid. paperqa_default was the default for each ablation, and
grobid was used for WikiCrow generation. This parameter was also varied in the experiments shown in
Figure 6.
• parsing_configuration.chunksize: the chunk size (in characters) to be used when chunking parsed
documents. This parameter was varied in the experiments shown in Figure 6.
• parsing_configuration.overlap: the overlap (in characters) that will be common between sequential
chunks. This was fixed at 750 for this work.
• parsing_configuration.chunking_algorithm: the algorithm used to chunk documents,
simple_overlap simply uses a sliding window with overlap, and sections uses semantic parsing by section (i.e. one chunk per section where possible), if sections need to be broken into
multiple chunks the system will automatically handle this. sections is only supported via
parsing_configuration.ordered_parser_preferences=grobid. This parameter was varied in
the experiments shown in Figure 6, and in our WikiCrow generation.
• temperature: temperature used for the LLM in the generate answer tool. This was set to 0 for all runs in this
work.
• summary_temperature: temperature used for the LLM in the gather evidence tool’s RCS step, this was set
to 0 for all runs in this work.
8.1.1 Tool implementations
PaperQA2’s agentic tools were implemented as in PaperQA4
. Our agent was prompted with the following message to
guide tool usage:
Answer question: {question}. Search for papers, gather evidence, collect papers cited in evidence
then re-gather evidence, and answer. Gathering evidence will do nothing if you have not done a new
search or collected new papers. If you do not have enough evidence to generate a good answer, you
can:
- Search for more papers (preferred)
- Collect papers cited by previous evidence (preferred)
- Gather more evidence using a different phrase
If you search for more papers or collect new papers cited by previous evidence, remember to gather
evidence again. Once you have five or more pieces of evidence from multiple sources, or you have
tried a few times, call {gen_answer_tool_name} tool. The {gen_answer_tool_name} tool output is
visible to the user, so you do not need to restate the answer and can simply terminate if the answer
looks sufficient. The current status of evidence/papers/cost is {status}
Where variables like {status} are included to represent the current state to the agent. Tools were implemented with the
following prompts and settings.
Paper Search Tool
13
Language Agents Achieve Superhuman Synthesis of Scientific Knowledge
The paper search tool uses an initial keyword search, generated by the agent in the context of the user query. The agent
is prompted as follows:
A search query in this format: [query], [start year]-[end year]. You may include years as the last word
in the query, e.g. ’machine learning 2020’ or ’machine learning 2010-2020’. The current year is
{get_year()}. The query portion can be a specific phrase, complete sentence, or general keywords, e.g.
’machine learning for immunology’.
Our initial search relies on services like Semantic Scholar 5
, where candidate lists (default of 12) of relevant papers
are generated then parsed. When parsed, the papers are first turned into text using either Grobid or PyMuPDF, then
split into chunksize character sized pieces. If the sections parsing is used, then section chunks are split on header
metadata provided by Grobid. An embedding vector is generated for each chunk using a hybrid implementation
which concatenates a dense and sparse, keyword based embedding model. For the experiments included in this study,
OpenAI’s text-embedding-large-3 was used. It was concatenated with a normalized 256 dimension vector which
used modulus-encoding to extract a hot-encoded keyword from the tokenization integers provided by OpenAI’s
tiktoken6
library. These text chunks are put into a document context which is accessible by the agent for further
manipulation with tools. The PaperQA entrypoint for these functions can be found on github.

Gather Evidence Tool
As detailed in github for PaperQA, the Gather Evidence tool begins with a top-k vector ranking step, using the
embedding vectors created in the Paper Search tool. The user query is embedded with the same algorithm, and cosine
similarity is used to match all document chunks in the agent’s context with the user query. The top-k chunks are then
selected for the RCS step.
The reranking and contextual summarization step most differentiates PaperQA’s implementation relative to other
RAG technologies. The tool’s prior step, an top-k vector retrieval ranking, is a widely implemented7,8 approach to
identify relevant documents, however, the RCS second step, is unique to PaperQA (to the authors’ knowledge). While
performance improvements with both deep reranking (or LLM) models and map-reduced summarizations 9,10,11,12 are
well documented, combining the reranking operation with a contextual summary provides novel benefits.
The step is implemented by mapping an LLM completion across each top-k chunk (system prompt):
Provide a summary of the relevant information that could help answer the question based on the
excerpt. The excerpt may be irrelevant. Do not directly answer the question - only summarize relevant
information. Respond with the following JSON format: {{ "summary": "...",
"relevance_score": "..." }} where "summary" is relevant information from text - {summary_length}
words and "relevance_score" is the relevance of "summary" to answer the question (integer out of 10)
Where each chunk is injected as follows:
Excerpt from citation —- {text} —- Query: {question}
After completion, each JSON object is parsed and the passages are re-ranked according to the new relevance scores.
When running with WikiCrow, gene names are also prompted to be extracted as additional JOSN keys, these are kept
and injected in the final answering context. Advantages of the RCS step are as follows: 1. Token usage efficiency
is vastly improved, a contextual summary will be 200-400 tokens compared with our standard document’s chunk
size of 2,250 tokens. This allows for a significantly more accessible document corpus for injection into PaperQA’s
answering context window. Furthermore, we see no decrease in summarization efficacy, using LitQA performance
as a proxy, across document chunk sizes from 750-3,000 tokens. 2. As a new feature in this work, the LLM can be
prompted to provide its summary in a structured JSON or XML format to simplify its downstream data extraction.
In addition to a relevance score used for reranking, this structure can include metadata (such as a gene name) which
will be retained through the PaperQA workflow. This is used to reduce hallucination and confusion in the final
answer context. Since the RCS step is performed in an embarrassingly parallel fashion, it’s highly efficient, and
its utility can be applied to an arbitrarily deep ranking, up to the rate or cost limits of the LLM API. Our studies
on the efficacy of the RCS depth led us to use a much deeper RCS depth, and to utilize the best performing model
for the RCS operation. This differs from the intuition in prior work4
, which utilized a cheaper model during the RCS step.
14
Language Agents Achieve Superhuman Synthesis of Scientific Knowledge
Generate Answer Tool
This tool answers questions by taking a subset of the top ranked sources (from the RCS ranking), and injects them into
a final context for answering. The default in this study was to inject 15 contextual summaries, but we saw maximal
accuracy with 5 at the cost of precision. LLMs were prompted to answer as follows:
Answer the question below with the context.
Context: {context} —- Question: {question}
Write an answer based on the context. If the context provides insufficient information and the question
cannot be directly answered, reply "I cannot answer." For each part of your answer, indicate which
sources most support it via citation keys at the end of sentences, like (Example2012Example pages
3-4). Only cite from the context and only use the valid keys. Write in the style of a Wikipedia article,
with concise sentences and coherent paragraphs. The context comes from a variety of sources and
is only a summary, so there may inaccuracies or ambiguities. If quotes are present and relevant,
use them in the answer. This answer will go directly onto Wikipedia, so do not add any extraneous
information.
Answer ({answer_length}):
Where contexts are injected by the generate answer code before output is returned to the agent.
Citation Traversal Tool
Atop the PaperQA4
tools, we created an additional tool to traverse one degree of citations, both forward in time (“future
citers”) and backwards in time (“past references”). This tool enables a fine-grained search around paper(s) containing
relevant information. The traversal originates from any paper containing a highly-scored contextual summary (RCS
score 0-10), and our minimum score threshold was eight (inclusive). The papers corresponding to highly-scored
summaries are referred to as Dprev in Algorithm 1. See Table 1a for the frequencies of various |Dprev| when this tool
was selected.
Table 1: Various statistics on citation traversal.
|Dprev| 1 2 3 4 5 6 7 8 9 10
Count 2147 941 530 386 307 216 154 67 29 12
Frequency (%) 44.8 19.6 11.1 8.1 6.4 4.5 3.2 1.4 0.6 0.3
(a) Distribution of traversal starting paper count |Dprev|.
|Dprev| 1 2 3 4 5 6 7+
Frequency of 1 Overlap (%) 100.0 91.3 91.7 90.8 90.8 88.7 87.5
Frequency of 2 Overlaps (%) 8.7 7.4 7.5 7.2 8.2 8.5
Frequency of 3 Overlaps (%) 0.9 1.4 1.4 2.0 2.4
Frequency of 4 Overlaps (%) 0.3 0.5 0.7 0.9
Frequency of 5 Overlaps (%) 0.1 0.3 0.4
Frequency of 6 Overlaps (%) 0.1 0.2
Frequency of 7+ Overlaps (%) 0.1
(b) Table showing the frequencies of citation overlap o seen in LitQA, illustrating the percentage of traversed citations at stake when
filtering with an overlap threshold θo . We chose to specify θo = ⌈α × |Dprev|⌉, where α is known as the overlap fraction and was
defaulted to 1
3
. The bolded values show what overlaps would have been preserved using an α =
1
3
.
To first acquire citations, Semantic Scholar 5
and Crossref 13 APIs are called for past references and Semantic Scholar
APIs are called for future citers. To collect all citations for a given paper, we make one API call per provider per
direction, totalling four API calls/paper. All three providers only provide partial paper details, meaning a large fraction
of the time a title or DOI is not present in the response metadata. To merge citations across providers, a best-effort
de-duplication is performed using casefolded title and lowercased DOI. In Algorithm 1, this logic takes place inside
the GetCitations procedure.
Once citations have been acquired, bins of overlap B are computed. For example, traversing past references for the following six DOIs: 10.1016/j.mcn.2006.08.007, 10.1002/cpsc.17, 10.1002/(sici)1098-
15
Language Agents Achieve Superhuman Synthesis of Scientific Knowledge
1136(200004)30:2<105::aid-glia1>3.0.co;2-h, 10.1089/scd.2015.0244, 10.1002/glia.22882, and
10.1042/an20120041, leads to one DOI cited by four papers, five DOIs cited by three papers, 29 DOIs cited
by two papers, and 428 DOIs cited by just one paper.
To filter bins of overlap, a hyperparameter “overlap fraction” α was introduced to compute a threshold overlap θo as a
function of the number of source papers (|Dprev|). For example, with an α =
2
5
and traversing from six source DOIs, all
citations not appearing in at least three source DOIs were discarded. The default overlap fraction used in data collection
was 1
3
. See Table 1b for a full distribution of overlaps seen during LitQA runs. Furthermore, a twelve paper limit ℓ was
posed on the traversal, which meant in the above example only keeping six of the bin of 29 DOIs cited by two papers.
To filter within a bin, we fall back on the count of future citers. This winnowing logic is detailed across Algorithm
1’s FilterOverlap and TraverseCitations procedures. Lastly, we traverse both future citers and past references,
feathering together the resultant DOIs before finding them.
Algorithm 1 Traverse Citations
Require: Set of summaries S, score threshold θscore, overlap fraction α, look future flag 1fut, limit ℓ
Ensure: Set of traversed papers Dout, where papers are future citers if 1fut else past references
1: procedure TRAVERSECITATIONS(S, θscore, α, 1fut, ℓ)
2: Dprev ← {sd | s ∈ S ∧ sscore ≥ θscore} ▷ Traverse from highly-scored summaries’ corresponding papers
3: D ← GETCITATIONS(Dprev, 1fut) ▷ D is a set of sets of papers such that |D| = |Dprev|
4: θo ← ⌈α × |D|⌉ ▷ Overlap threshold θo scales with |S|
5: return FILTEROVERLAP(D, Dprev, θo, ℓ)
Require: Set of sets of candidate papers D, set of previous papers Dprev, (inclusive) overlap threshold θo, limit ℓ
Ensure: Set of filtered papers Dout
6: procedure FILTEROVERLAP(D, Dprev, θo, ℓ)
7: B =
o, {d |
P
D∈D 1(d ∈ D)

= o}

for o ∈ [|D|, . . . , 1]
▷ Bin papers according to decreasing overlap
8: Dout ← {}
9: for o, D ∈ B do ▷ Highest overlapping citations come first
10: if o < θo ∨ |Dout| ≥ ℓ then break
11: D ← {d | d ∈ D ∧ d /∈ Dprev} ▷ Filter out already present papers
12: if ℓ − |Dout| < |D| then ▷ If the entire bin won’t fit within limit ℓ
13: D ← {d | i, d ∈ SORT↓CITERS(D) ∧ i ≤ (ℓ − |Dout|)} ▷ Keep subset with the most future citers
14: Dout ← Dout ∪ D
15: return Dout
```