# OpenAlex Full-Text Resolution Flows

These diagrams cover the two main entry points into the resolver: directly from a `pdf_url` hint and from an HTML `landing_page_url`.

## Graph 1 - Start from OpenAlex `pdf_url`

```mermaid
flowchart TB
  %% --- START & PREP ---
  A0([Start: from OpenAlex pdf_url]) --> A1[Prep session:<br/>- UA like modern browser<br/>- Cookies jar<br/>- Timeouts (connect/read)<br/>- Redirect cap=8<br/>- Per-host rate limit<br/>- Accept: text/html,application/pdf;q=0.9,*/*;q=0.8]
  A1 --> A2[GET pdf_url (stream=True, Range: 0-4095 bytes)]

  %% --- HTTP STATUS LAYER ---
  A2 -->|Network/DNS/TLS error| S_NET[Network/TLS error<br/>-> retry w/ backoff; alt DNS/IP; log]
  A2 -->|HTTP 429| S_429[429 rate limited<br/>-> jittered backoff; respect Retry-After; per-host throttle]
  A2 -->|HTTP 5xx| S_5XX[5xx server error<br/>-> exponential backoff; limited retries; consider alt location]
  A2 -->|HTTP 404/410| S_404[Dead link<br/>-> try other OpenAlex locations immediately]
  A2 -->|HTTP 451| S_451[Legal restriction<br/>-> try repositories/preprints; log reason]
  A2 -->|HTTP 3xx| S_3XX{Follow redirects <=8}
  A2 -->|HTTP 401/403| S_403{Auth/Forbidden}

  %% --- 3XX BRANCH ---
  S_3XX --> S3A[Final target after redirects]
  S3A -->|Login/SSO path| S_AUTH[Auth required<br/>-> proxy/headless if lawful; else repository fallback]
  S3A -->|HTML landing| H0[Handle as HTML (viewer/real/SPA) with same session + referer]
  S3A -->|PDF/Octet-stream| P0[Handle as PDF]

  %% --- 403 BRANCH ---
  S_403 -->|No Referer yet & landing_page_url known| R1[Retry GET with Referer=landing_page_url; same session]
  R1 --> R1S{Status after retry}
  R1S -->|200| INSPECT
  R1S -->|3xx| S_3XX
  R1S -->|401/403| S_AUTH
  S_403 -->|Already tried Referer or none available| S_AUTH

  %% --- 200 OK -> INSPECT ---
  A2 -->|HTTP 200| INSPECT{{Inspect headers + first KB}}
  INSPECT -->|CT=application/pdf OR bytes start with %PDF-| P0
  INSPECT -->|CT=application/octet-stream AND bytes %PDF-| P0
  INSPECT -->|CT=text/html AND NOT %PDF-| H0
  INSPECT -->|Unknown CT AND %PDF-| P0
  INSPECT -->|Unknown CT AND NOT %PDF-| H0

  %% --- PDF HANDLING SUBGRAPH ---
  subgraph "PDF Handling"
    P0[Treat as PDF] --> P1[Filename: from Content-Disposition<br/>(supports RFC 5987 filename*=), else derive]
    P0 --> P2{Supplementary?}
    P2 -->|Heuristics hit:<br/>URL/filename mentions supplement/ESM/SI/appendix,<br/>or first-page DOI/title mismatch| P2A[Attempt main-article discovery:<br/>- use landing_page_url DOM<br/>- prefer 'Download PDF' / 'Article PDF'<br/>- else switch to OpenAlex location list]
    P2A --> P0
    P2 -->|Looks like main article| P3[Optional identity check:<br/>extract first-page text -> DOI/title fuzzy match]
    P3 --> P4[Save stream to disk:<br/>final_url, CT, length, SHA256]
    P4 --> P5[Record provenance:<br/>ETag, Last-Modified, redirect chain, referer_used, cookies_used]
    P5 --> P6{Tokenized/signed URL? (query=token=..., aws sig, etc.)}
    P6 -->|Yes| P6A[Mark ephemeral:<br/>do not cache URL; re-resolve via OpenAlex/landing when needed]
    P6 -->|No| P7((OK_PDF))
  end

  %% --- HTML/VIEWER HANDLING SUBGRAPH ---
  subgraph "HTML / Viewer Handling"
    H0[HTML response reached] --> H1{Classify DOM}
    H1 -->|Viewer (pdf.js, iframe/embed,<br/>object, viewer.html?file=)| V1[Extract underlying PDF URL:<br/>file= param or iframe src]
    V1 --> V2[GET underlying PDF with Referer=viewer_url; same session]
    V2 --> INSPECT2{{Inspect headers + bytes}}
    INSPECT2 -->|PDF| P0
    INSPECT2 -->|HTML again| V3[Edge viewer -> run headless to capture network PDF] --> V4{Captured PDF?}
    V4 -->|Yes| P0
    V4 -->|No| H4[HTML-only fallback]

    H1 -->|Real article HTML (rich DOM)| RHTML[Save HTML; mine links:<br/>- &lt;link rel="alternate" type="application/pdf"><br/>- meta citation_pdf_url<br/>- 'Download PDF' anchors<br/>- JATS/XML]
    RHTML --> LNK{Found JATS or PDF?}
    LNK -->|JATS/XML| X1[Fetch XML; prefer JATS; parse license & assets] --> XOK((OK_XML/HTML))
    LNK -->|PDF| L1[GET PDF with Referer=landing_url] --> INSPECT3{{Inspect headers + bytes}}
    INSPECT3 -->|PDF| P0
    INSPECT3 -->|HTML| H4
    LNK -->|None| H4

    H1 -->|SPA/JS-only shell| SPA1[Try print endpoints:<br/>?format=print, /print, /pdf]
    SPA1 --> SPA2{Found server-side print/pdf?}
    SPA2 -->|Yes| SPA3[GET that endpoint] --> INSPECT4{{Inspect headers + bytes}}
    INSPECT4 -->|PDF| P0
    INSPECT4 -->|HTML| H4
    SPA2 -->|No| SPA4[Headless (Playwright): click 'PDF', capture network]
    SPA4 --> SPA5{Captured PDF?}
    SPA5 -->|Yes| P0
    SPA5 -->|No| H4

    H1 -->|Paywall/Abstract-only| PW[Publisher path blocked<br/>-> use alternates]
  end

  %% --- ALTERNATES & RECOVERY SUBGRAPH ---
  subgraph "Alternates & Recovery"
    S_AUTH[Auth required / SSO]<--> ALT
    S_404 --> ALT
    S_451 --> ALT
    S_5XX --> ALT
    S_429 --> ALT
    S_NET --> ALT
    PW --> ALT
    H4[HTML-only journal or no PDF link]<--> ALT

    ALT[Use OpenAlex location list other than current:<br/>prefer repositories (PMC/Europe PMC, arXiv, HAL, Zenodo, OSF).<br/>If any_repository_has_fulltext=true -> go green.] --> ALT2{Repository found?}
    ALT2 -->|Yes| REP1[Repository flow]
    REP1 --> REPXML{JATS/XML available?}
    REPXML -->|Yes| X1
    REPXML -->|No| REPPDF[GET repository PDF] --> INSPECT5{{Inspect headers + bytes}} -->|PDF| P0
    ALT2 -->|No| CLOSED((CLOSED / manual library resolver))

    %% Retries
    S_429 --> RETRY1[Respect Retry-After; jitter; capped retries] --> A2
    S_5XX --> RETRY2[Exponential backoff; alt host if any] --> A2
    S_NET --> RETRY3[Retry + alternate DNS/IPv4/IPv6; log] --> A2
  end

  %% --- TERMINALS ---
  P7 --> END1([END: OK_PDF])
  XOK --> END2([END: OK_XML/HTML])
  CLOSED --> END3([END: Unavailable via OA routes])
```

## Graph 2 - Start from OpenAlex `landing_page_url` (HTML)

```mermaid
flowchart TB
  %% --- START & PREP ---
  B0([Start: from OpenAlex landing_page_url (HTML)]) --> B1[Prep session:<br/>- UA like modern browser<br/>- Cookies jar<br/>- Timeouts<br/>- Redirect cap=8<br/>- Per-host rate limit]
  B1 --> B2[GET landing_page_url (stream=True)]

  %% --- HTTP STATUS LAYER ---
  B2 -->|Network/DNS/TLS error| BN[S/TLS error -> retry/backoff; alt DNS/IP; log]
  B2 -->|HTTP 429| B429[429 -> jittered backoff; respect Retry-After]
  B2 -->|HTTP 5xx| B5XX[5xx -> backoff & retry; consider alternates]
  B2 -->|HTTP 404/410| B404[Dead landing -> try other OpenAlex locations]
  B2 -->|HTTP 451| B451[Legal restriction -> try repositories/preprints; log]
  B2 -->|HTTP 3xx| B3XX{Follow redirects <=8}
  B2 -->|HTTP 401/403| B403[Forbidden/Auth -> alternates or lawful proxy/headless]
  B2 -->|HTTP 200| B200{{CT & DOM pre-check}}

  %% --- REDIRECTS ---
  B3XX --> B3A[Final target after redirects]
  B3A -->|Login/SSO path| B403
  B3A -->|HTML| B200
  B3A -->|PDF/Octet-stream| P0[Treat as PDF] --> PINS{{Inspect headers + bytes}}
  PINS -->|PDF| POK((OK_PDF))
  PINS -->|HTML| B200

  %% --- DOM CLASSIFICATION ---
  B200 -->|CT=text/html| C0{Classify HTML}
  B200 -->|CT=application/pdf or bytes %PDF-| POK

  C0 -->|Viewer page (pdf.js / iframe/embed / object / viewer.html?file=)| C_VIEW[Extract underlying PDF URL from file= or iframe src]
  C_VIEW --> C_VIEW2[GET underlying PDF with Referer=viewer_url; same session] --> C_VIEW3{{Inspect headers + bytes}}
  C_VIEW3 -->|PDF| POK
  C_VIEW3 -->|HTML again| C_VIEW4[Edge viewer -> headless capture of network PDF] --> C_VIEW5{Captured PDF?}
  C_VIEW5 -->|Yes| POK
  C_VIEW5 -->|No| C_HTMLONLY[Fallback: HTML-only]

  C0 -->|Real article (rich DOM)| C_REAL[Save HTML; mine links:<br/>- &lt;link rel="alternate" type="application/pdf"><br/>- meta name="citation_pdf_url"<br/>- visible 'Download PDF' anchors<br/>- JATS/XML links]
  C_REAL --> C_LINKS{Found JATS/XML or PDF?}
  C_LINKS -->|JATS/XML| C_XML[Fetch XML (prefer JATS); parse license; gather figures] --> OK_XML((OK_XML/HTML))
  C_LINKS -->|PDF| C_PDF[GET PDF with Referer=landing_url] --> C_PDF2{{Inspect headers + bytes}}
  C_PDF2 -->|PDF| C_PDF3[Check supplementary trap:<br/>URL/name hints or first-page DOI/title] --> C_PDF4{Supplement?}
  C_PDF4 -->|Yes| C_FINDMAIN[Locate main-article PDF via DOM; else use OpenAlex alternates] --> C_PDF
  C_PDF4 -->|No| POK
  C_LINKS -->|None| C_HTMLONLY[If journal is HTML-only -> treat HTML as canonical result]

  C0 -->|SPA/JS-only app| C_SPA[Try print endpoints (?format=print, /print, /pdf)]
  C_SPA --> C_SPA2{Server-side print/pdf exists?}
  C_SPA2 -->|Yes| C_SPA3[GET print/pdf endpoint] --> C_SPA4{{Inspect headers + bytes}}
  C_SPA4 -->|PDF| POK
  C_SPA4 -->|HTML| C_HTMLONLY
  C_SPA2 -->|No| C_SPA5[Headless (Playwright): click 'PDF' or 'Download'; capture network]
  C_SPA5 --> C_SPA6{Captured PDF?}
  C_SPA6 -->|Yes| POK
  C_SPA6 -->|No| C_HTMLONLY

  C0 -->|Paywall/Abstract-only| C_PW[Blocked at publisher<br/>-> use alternates]

  %% --- ALTERNATES & RECOVERY ---
  C_PW --> ALT
  B403 --> ALT
  B404 --> ALT
  B451 --> ALT
  B5XX --> ALT
  B429 --> ALT
  BN --> ALT
  C_HTMLONLY --> ALT

  subgraph "Alternates & Repository Preference"
    ALT[Use OpenAlex location list other than current:<br/>prefer repositories (PMC/Europe PMC, arXiv, HAL, Zenodo, OSF).<br/>If any_repository_has_fulltext=true -> go green.] --> ALT2{Repository found?}
    ALT2 -->|Yes| R1[Repository flow]
    R1 --> RXML{JATS/XML available?}
    RXML -->|Yes| OK_XML
    RXML -->|No| RPDF[GET repository PDF] --> RPDF2{{Inspect headers + bytes}} -->|PDF| POK
    ALT2 -->|No| RCLOSED((CLOSED / manual library resolver))
  end

  %% --- OUTPUTS & LOGGING ---
  POK --> L0[Persist:<br/>file + metadata (CT, length, SHA256,<br/>ETag, Last-Modified, redirects, referer, cookies)]
  OK_XML --> L1[Persist XML/HTML + license & assets]
  L0 --> END1([END: OK_PDF])
  L1 --> END2([END: OK_XML/HTML])
  RCLOSED --> END3([END: Unavailable via OA routes])
```
