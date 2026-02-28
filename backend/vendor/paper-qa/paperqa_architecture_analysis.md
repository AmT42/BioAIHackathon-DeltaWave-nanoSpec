# PaperQA Architecture Analysis: OSS vs Real Implementation

## Table of Contents
1. [Overview](#overview)
2. [Architecture Flows](#architecture-flows)
   - [Open Source Version Flow](#open-source-version-flow)
   - [Real Implementation Flow](#real-implementation-flow)
3. [Key Components Analysis](#key-components-analysis)
4. [Understanding <1k Chunks and Vector Stores](#understanding-1k-chunks-and-vector-stores)
5. [Tantivy Integration with OpenAlex](#tantivy-integration-with-openalex)
6. [Detailed Example: Agent Query Flow](#detailed-example-agent-query-flow)
7. [Implementation Recommendations](#implementation-recommendations)

## Overview

PaperQA implements a **two-stage retrieval system** combining keyword search with semantic search:

1. **Tantivy for paper-level keyword search**: Finding relevant papers from a document corpus
2. **Vector stores (Numpy/Qdrant) for semantic chunk retrieval**: Finding relevant text chunks within papers
3. **LLM-based evidence summarization**: Creating scored context summaries from retrieved chunks
4. **MMR (Maximal Marginal Relevance)**: Reducing redundancy in semantic search results

## Architecture Flows

### Open Source Version Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     USER QUERY                               │
│                  "What is RAG?"                              │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│              AGENT: PaperSearch Tool                         │
│  • Input: query="RAG retrieval augmented generation"         │
│  • Uses: Tantivy SearchIndex on LOCAL PDF directory          │
│  • Index contains: title, year, body (concatenated chunks)  │
│  • Returns: Top 8 matching Docs objects                     │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│           ADD TO DOCS COLLECTION                             │
│  • For each returned paper:                                  │
│  • Extract ALL chunks (typically 20-50 per paper)            │
│  • Add to Docs.texts list (no embeddings yet)               │
│  • Total texts: ~160-400 chunks from 8 papers               │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│          AGENT: GatherEvidence Tool                          │
│  • Calls Docs.aget_evidence()                                │
│  • First time: builds texts_index (embeds ALL chunks)        │
│  • Vector search with MMR on 160-400 chunks                  │
│  • Returns top-10 chunks (evidence_k=10)                     │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│         LLM SUMMARIZATION (Parallel)                         │
│  • For each of 10 chunks:                                    │
│  • Send to LLM with question                                 │
│  • Get summary + relevance score (0-10)                      │
│  • Filter: keep only score > 0                               │
│  • Result: ~5-8 relevant contexts                            │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│      CHECK: Enough Evidence?                                 │
│  • If contexts < 3 or low scores:                            │
│  • RETRY: paper_search with different keywords               │
│  • Offset: skip first 8 papers, get next 8                  │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│         AGENT: GenerateAnswer Tool                           │
│  • Combine all contexts                                      │
│  • Send to LLM with structured prompt                        │
│  • Generate final answer with citations                      │
└─────────────────────────────────────────────────────────────┘
```

### Real Implementation Flow

Based on the paper, the real PaperQA implementation likely includes:

```
┌─────────────────────────────────────────────────────────────┐
│                     USER QUERY                               │
│                  "What is RAG?"                              │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│           AGENT: PaperSearch Tool (ENHANCED)                 │
│  • Query Expansion: "RAG" → multiple search queries          │
│  • OpenAlex API: semantic + keyword search                   │
│  • Filters: year>2020, open_access=true, cited_by>10        │
│  • Returns: 20-50 paper metadata (no PDFs yet)               │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│         PAPER RANKING & FILTERING                            │
│  • LLM scores paper abstracts for relevance                  │
│  • Citation graph traversal (find related papers)            │
│  • Dedup by DOI/title similarity                             │
│  • Select top 8-10 papers to download                        │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│           PDF RETRIEVAL & PROCESSING                         │
│  • Download PDFs from OpenAlex/ArXiv/Publisher               │
│  • Parse with Grobid/PyMuPDF                                 │
│  • Extract: text, figures, tables, equations                 │
│  • Smart chunking: preserve semantic boundaries              │
│  • Media enrichment: describe figures with VLM               │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│      HYBRID SEARCH (Tantivy + Embeddings)                    │
│  • Tantivy: keyword search on CHUNKS (not full papers)       │
│  • Filter chunks by keyword relevance first                  │
│  • Reduces from 500+ chunks to <100 candidates               │
│  • Then embed only these 100 chunks                          │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│        SEMANTIC SEARCH + RERANKING                           │
│  • Vector search on pre-filtered chunks                      │
│  • Cross-encoder reranking (BERT-based)                      │
│  • MMR for diversity (lambda=0.7)                            │
│  • Returns top-15 chunks                                     │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│      EVIDENCE GENERATION WITH CHAIN-OF-THOUGHT               │
│  • Each chunk → detailed CoT reasoning                       │
│  • Extract: claims, methodology, limitations                 │
│  • Score: relevance, reliability, recency                    │
│  • Keep top 8-10 contexts                                    │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│           ITERATIVE REFINEMENT                               │
│  • If evidence insufficient:                                 │
│  • Targeted search: follow citations from good papers        │
│  • Query reformulation based on found evidence               │
│  • Domain-specific search (e.g., PubMed for medical)         │
└────────────────┬───────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│         ANSWER SYNTHESIS WITH VERIFICATION                   │
│  • Multi-turn answer generation                              │
│  • Fact checking against sources                             │
│  • Contradiction detection                                   │
│  • Confidence scoring                                        │
└─────────────────────────────────────────────────────────────┘
```

## Key Components Analysis

### 1. Paper Search with Tantivy (OSS Version)

**File**: `src/paperqa/agents/search.py` (lines 114-743)

#### SearchIndex Class
- **Purpose**: Wrapper around Tantivy's full-text search index for document-level keyword search
- **Storage Location**: Index files stored in `index_directory/index_name/` structure
- **Required Fields**: `["file_location", "body"]`
- **Additional Indexed Fields**: `title`, `year`
- **Document Storage**: Serialized Docs objects stored separately from index using pickle compression

#### Query Mechanism
```python
# Clean query of special chars
cleaned_query = CLEAN_QUERY_REGEX.sub("", query)
# Parse with Tantivy across specified fields
parsed_query = index.parse_query(cleaned_query, query_fields)
# Search with pagination
addresses = searcher.search(parsed_query, top_n, offset=offset).hits
# Retrieve stored Docs objects
results = await self.get_saved_object(doc["file_location"][0])
```

### 2. Evidence Gathering Flow

**File**: `src/paperqa/docs.py` (lines 633-736)

#### The Complete Evidence Pipeline:

1. **Semantic Retrieval**
```python
matches = await self.retrieve_texts(
    session.question,
    answer_config.evidence_k,  # Default: 10 chunks
    evidence_settings,
    embedding_model,
    partitioning_fn=partitioning_fn,
)
```

2. **LLM-Based Summarization**
```python
results = await gather_with_concurrency(
    answer_config.max_concurrent_requests,  # Default: 4
    [map_fxn_summary(...) for m in matches]
)
```

3. **Context Filtering**
```python
session.contexts += [c for c, _ in results if c is not None and c.score > 0]
```

### 3. Vector Store Implementations

#### NumpyVectorStore (Default)
- **Storage**: In-memory list of Text objects
- **Embeddings**: Numpy array built on first use
- **Similarity Search**: Brute force cosine similarity
- **Time Complexity**: O(n × d) where n=chunks, d=embedding_dim(1536)

#### QdrantVectorStore (Optional)
- **Storage**: Remote or in-memory Qdrant instance
- **Indexing**: HNSW for approximate nearest neighbor
- **Time Complexity**: O(log n)
- **Use Case**: When chunks > 10,000

## Understanding <1k Chunks and Vector Stores

### Why <1k Chunks Matters

The key insight is about **working set size** after initial keyword filtering:

```python
# After paper_search with Tantivy:
8 papers × 30 chunks/paper = 240 chunks (typical case)
8 papers × 100 chunks/paper = 800 chunks (large papers)

# This is your ACTIVE working set for semantic search
```

### Numpy vs Qdrant Decision Tree

```
┌──────────────────────────────────────┐
│   How many chunks after filtering?    │
└─────────────┬────────────────────────┘
              │
    ┌─────────┴─────────┐
    │                   │
    ▼                   ▼
 < 1000 chunks      > 10,000 chunks
    │                   │
    ▼                   ▼
┌──────────────┐   ┌──────────────┐
│ Use Numpy    │   │ Use Qdrant   │
│              │   │              │
│ WHY:         │   │ WHY:         │
│ • All in RAM │   │ • Disk-based │
│ • No index   │   │ • HNSW index │
│ • O(n) search│   │ • O(log n)   │
│ • Fast for   │   │ • Efficient  │
│   small n    │   │   for large n│
└──────────────┘   └──────────────┘
```

### Performance Comparison

**NumpyVectorStore**:
```python
# For 1000 chunks:
1000 × 1536 × 4 bytes = 6.1 MB in RAM
Search time: ~10ms (brute force is fast at this scale!)
```

**QdrantVectorStore**:
```python
# For 100,000 chunks:
100k × 1536 × 4 bytes = 614 MB + index overhead
Search time: ~5ms (but index build takes minutes)
```

### Design Philosophy

PaperQA's two-stage approach **intentionally keeps chunks under 1k** because:

1. **Keyword filtering is cheap**: Tantivy can search millions of papers in milliseconds
2. **Embedding is expensive**: Only embed chunks from relevant papers
3. **Brute force is fine at small scale**: No index overhead for <1k vectors
4. **Memory is predictable**: 8 papers × 125 chunks max = 1000 chunks = 6MB RAM

## Tantivy Integration with OpenAlex

### Should You Keep Tantivy with OpenAlex?

**Answer: YES, but repurpose it!**

| Aspect | OSS Version | With OpenAlex |
|--------|-------------|---------------|
| **What Tantivy Indexes** | Full paper bodies (concatenated chunks) | Individual chunks |
| **When Used** | BEFORE downloading papers | AFTER downloading papers |
| **Purpose** | Find relevant papers in local directory | Filter chunks within downloaded papers |
| **Scale** | 100s-1000s of papers | 1000s-10,000s of chunks |

### Proposed Architecture with OpenAlex

```python
# STAGE 1: Paper Discovery (OpenAlex replaces Tantivy here)
def paper_search_with_openalex(query):
    # Use OpenAlex API for paper discovery
    results = openalex.search(
        query=query,
        filters={
            'publication_year': '>2020',
            'is_oa': True,
            'cited_by_count': '>5'
        }
    )

    # Download PDFs for top papers
    for paper in results[:10]:
        pdf = download_pdf(paper.pdf_url)
        chunks = parse_and_chunk(pdf)

        # ADD CHUNKS TO TANTIVY INDEX (NEW!)
        for chunk in chunks:
            tantivy_index.add_document({
                'paper_id': paper.id,
                'chunk_id': chunk.id,
                'text': chunk.text,
                'paper_title': paper.title,
                'section': chunk.section  # "Introduction", "Methods", etc.
            })

    return chunks

# STAGE 2: Chunk Filtering (Tantivy NEW role)
def gather_evidence(query, all_chunks):
    # Use Tantivy for keyword filtering on CHUNKS
    keyword_relevant_chunks = tantivy_index.search(
        query=query,
        limit=200  # Reduce from 1000s to 200
    )

    # Only embed these 200 chunks (not all 1000s)
    embeddings = embed(keyword_relevant_chunks)

    # Semantic search on pre-filtered set
    semantic_matches = vector_search(
        query_embedding,
        embeddings,
        k=10
    )

    return semantic_matches
```

### Benefits of Keeping Tantivy

1. **Chunk-Level Keyword Filtering**:
   - OpenAlex finds papers, but you still have 100+ chunks per paper
   - Tantivy can quickly filter to keyword-relevant chunks
   - Reduces embedding costs by 80%+

2. **Section-Aware Search**:
   ```python
   # Find methods sections about "transformer architecture"
   tantivy_index.search(
       query="transformer architecture",
       filter={'section': 'Methods'}
   )
   ```

3. **Hybrid Scoring**:
   ```python
   final_score = 0.3 * tantivy_bm25_score + 0.7 * cosine_similarity_score
   ```

4. **Caching Layer**:
   - Keep Tantivy index of previously downloaded papers
   - Avoid re-downloading/re-parsing PDFs

### When to Remove Tantivy

Only remove if:
- You have <50 papers total (just embed everything)
- You're using a reranker model that needs all chunks
- You have unlimited embedding API budget
- OpenAlex provides chunk-level search (it doesn't currently)

## Detailed Example: Agent Query Flow

### Scenario: "What are the latest advances in multimodal RAG?"

### First paper_search Call

```python
# STEP 1: Initial Query
agent.query("What are the latest advances in multimodal RAG?")
```

#### Flow Details:

1. **Agent Reasoning**: "Need papers about multimodal RAG, start with general search"

2. **paper_search Execution**:
   ```python
   paper_search(query="multimodal RAG retrieval augmented")
   ```

3. **Search Process**:
   - **OSS Version**: Query local Tantivy index
   - **OpenAlex Version**: API call with filters
   - Returns 8 papers

4. **Deduplication**:
   ```python
   for paper in results:
       if paper.dockey not in state.docs:
           state.docs[paper.dockey] = paper
   ```

5. **Chunk Extraction**:
   - Paper 1: 45 chunks (40 text, 5 figures)
   - Paper 2: 38 chunks (35 text, 3 tables)
   - ...Papers 3-8...
   - **Total**: 312 chunks added to state.texts

6. **Evidence Gathering**:
   - Build text index (first time only)
   - Embed all 312 chunks
   - Semantic search → top 10 chunks
   - LLM summarization (parallel, max 4)
   - Filter by score > 0
   - **Result**: 6 relevant contexts

7. **Agent Evaluation**: "Only 6 contexts, need more specific information"

### Second paper_search Call (Refinement)

```python
paper_search(query="CLIP BLIP multimodal benchmark",
            search_count=8, offset=8)
```

#### Key Differences in Second Call:

1. **Offset Usage**: Skip first 8 results, get next 8
2. **Deduplication Check**: Some papers might be duplicates
3. **Incremental Index Update**: Only embed NEW chunks
4. **Larger Search Pool**: Now searching across 552 chunks total

#### State After Second Search:
- **Papers**: 14 unique (6 new added)
- **Chunks**: 552 total (312 + 240 new)
- **Contexts**: 14 total (6 + 8 new)
- **Embeddings**: All cached in texts_index

### Final Answer Generation

```python
generate_answer(contexts=14 contexts)
```

Produces structured answer with citations from all gathered evidence.

## Implementation Recommendations

### Recommended Architecture for OpenAlex Integration

```python
class OpenAlexPaperQA:
    def __init__(self):
        self.openalex_client = OpenAlex()
        self.tantivy_index = SearchIndex()  # For CHUNKS, not papers
        self.vector_store = NumpyVectorStore()  # Under 1k chunks
        self.pdf_cache = {}  # Cache downloaded PDFs

    async def paper_search(self, query: str, offset: int = 0):
        """
        Stage 1: Paper Discovery via OpenAlex
        """
        # 1. Use OpenAlex for paper discovery
        papers = await self.openalex_client.search(
            query=query,
            filters={
                'is_oa': True,
                'publication_year': '>2020',
                'cited_by_count': '>5',
                'has_fulltext': True
            },
            per_page=10,
            page=offset // 10 + 1
        )

        # 2. Rank papers by relevance (optional LLM scoring)
        ranked_papers = await self._rank_papers(papers, query)

        # 3. Download and process PDFs
        chunks_added = []
        for paper in ranked_papers[:8]:
            if paper.doi in self.pdf_cache:
                chunks = self.pdf_cache[paper.doi]
            else:
                pdf = await self._download_pdf(paper)
                chunks = await self._parse_and_chunk(pdf)
                self.pdf_cache[paper.doi] = chunks

            # 4. Add chunks to Tantivy for keyword search
            for chunk in chunks:
                self.tantivy_index.add_document({
                    'doi': paper.doi,
                    'chunk_id': f"{paper.doi}_{chunk.index}",
                    'text': chunk.text,
                    'title': paper.title,
                    'section': chunk.section,
                    'year': paper.publication_year
                })

            chunks_added.extend(chunks)

        return chunks_added

    async def gather_evidence(self, query: str, k: int = 10):
        """
        Stage 2: Evidence Extraction with Hybrid Search
        """
        # 1. Keyword filtering with Tantivy (on chunks)
        keyword_chunks = self.tantivy_index.search(
            query=query,
            limit=200  # Pre-filter to 200 most relevant
        )

        # 2. Embed only keyword-relevant chunks (not all chunks)
        if not keyword_chunks:
            return []

        embeddings = await self._embed_texts(keyword_chunks)
        self.vector_store.add_texts(keyword_chunks, embeddings)

        # 3. Semantic search with MMR
        semantic_matches = await self.vector_store.mmr_search(
            query=query,
            k=k,
            lambda_mult=0.7  # Balance relevance and diversity
        )

        # 4. LLM summarization with scoring
        contexts = await self._summarize_chunks(semantic_matches, query)

        # 5. Filter by relevance score
        return [c for c in contexts if c.score > 0]

    async def _rank_papers(self, papers, query):
        """
        Optional: Use LLM to rank paper abstracts
        """
        # Implementation depends on your LLM setup
        pass

    async def _download_pdf(self, paper):
        """
        Download PDF from best available source
        """
        # Try sources in order: OA link, ArXiv, publisher
        pass

    async def _parse_and_chunk(self, pdf):
        """
        Smart chunking with section preservation
        """
        # Use PyMuPDF or Grobid for parsing
        # Preserve semantic boundaries
        pass
```

### Key Design Decisions

| Decision Point | Recommendation | Rationale |
|----------------|----------------|-----------|
| **Remove Tantivy?** | No, repurpose for chunk filtering | Reduces embeddings by 80% |
| **Vector Store Choice** | Numpy for <1k chunks, Qdrant for >10k | Memory vs performance tradeoff |
| **Index Target** | Index chunks, not papers | Enables fine-grained search |
| **MMR Lambda** | 0.7-0.9 | Balance relevance and diversity |
| **Chunk Size** | 300-500 tokens | Optimal for context windows |
| **Embedding Cache** | Session-level | Avoid re-embedding |
| **PDF Cache** | 24 hours | Reduce download overhead |

### Performance Optimizations

1. **Parallel Processing**:
   ```python
   # Download PDFs concurrently
   pdfs = await asyncio.gather(*[
       download_pdf(paper) for paper in papers[:10]
   ])
   ```

2. **Batch Embeddings**:
   ```python
   # Embed in batches of 50
   for i in range(0, len(chunks), 50):
       batch = chunks[i:i+50]
       embeddings = await embed_batch(batch)
   ```

3. **Smart Caching**:
   ```python
   # Cache OpenAlex results
   @lru_cache(maxsize=1000, ttl=86400)  # 24 hour TTL
   async def search_openalex(query, filters):
       return await openalex.search(query, filters)
   ```

4. **Incremental Indexing**:
   ```python
   # Don't rebuild entire index
   if chunk_id not in self.tantivy_index:
       self.tantivy_index.add_document(chunk)
   ```

5. **Section-Aware Chunking**:
   ```python
   # Preserve section boundaries
   chunks = []
   for section in paper.sections:
       section_chunks = chunk_text(
           section.text,
           max_tokens=400,
           overlap=50,
           preserve_sentences=True
       )
       chunks.extend(section_chunks)
   ```

### Error Handling

```python
async def robust_paper_search(self, query, retries=3):
    for attempt in range(retries):
        try:
            # Try OpenAlex
            papers = await self.openalex_search(query)
            if papers:
                return papers
        except OpenAlexAPIError:
            # Fallback to Semantic Scholar
            papers = await self.semantic_scholar_search(query)
            if papers:
                return papers
        except Exception as e:
            if attempt == retries - 1:
                # Final fallback: use cached results
                return self.get_cached_papers(query)
    return []
```

## Summary

The key insights for your OpenAlex integration:

1. **Keep Tantivy** but change its role from paper search to chunk filtering
2. **Two-stage retrieval** is critical for performance (coarse → fine)
3. **<1k chunks principle** makes Numpy viable, avoiding index overhead
4. **Hybrid search** (keyword + semantic) provides best results
5. **Cache aggressively** at multiple levels (PDFs, embeddings, API results)

This architecture gives you the best of both worlds: OpenAlex's massive paper coverage with efficient local search and filtering. The two-stage approach ensures you can handle millions of papers while keeping computational costs manageable.