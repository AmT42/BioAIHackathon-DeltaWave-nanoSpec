# Evidence Report: NAD+ Precursors (NMN / NR)

## 1) Intervention Identity
- Type: unknown
- Pivot: None identified
- Query: NAD+ Precursors (NMN / NR)
- Population: unspecified
- Outcome: unspecified
- Ambiguity notes:
  - None identified.
- Directness warnings:
  - None identified.

## 2) Evidence Pyramid
- Level 1 (systematic/meta): 6
- Level 2 (human interventional): 38
- Level 3 (human observational): 6
- Level 4 (animal in vivo): 7
- Level 5 (in vitro): 4
- Level 6 (in silico): 0

## 3) Key Human Evidence
- PMID:31917996 | 2020 | meta_analysis | clinical_hard | benefit | flags=none
  title: NAD+ therapy in age-related degenerative disorders: A benefit/risk analysis.
- PMID:39185644 | 2025 | meta_analysis | clinical_hard | unknown | flags=none
  title: Effects of Nicotinamide Mononucleotide Supplementation on Muscle and Liver Functions Among the Middle-aged and Elderly: A Systematic Review and Meta-analysis of Randomized Controlled Trials.
- PMID:37954044 | 2023 | meta_analysis | surrogate_biomarker | benefit | flags=indirect_endpoint
  title: Exercise training upregulates intracellular nicotinamide phosphoribosyltransferase expression in humans: a systematic review with meta-analysis.
- PMID:35920994 | 2022 | meta_analysis | clinical_hard | unknown | flags=none
  title: Impact of nutraceuticals and dietary supplements on mitochondria modifications in healthy aging: a systematic review of randomized controlled trials.
- PMID:40033213 | 2025 | meta_analysis | clinical_hard | benefit | flags=none
  title: A systematic review of the therapeutic potential of nicotinamide adenine dinucleotide precursors for cognitive diseases in preclinical rodent models.
- PMID:39116016 | 2025 | meta_analysis | clinical_intermediate | null | flags=none
  title: Efficacy of oral nicotinamide mononucleotide supplementation on glucose and lipid metabolism for adults: a systematic review with meta-analysis on randomized controlled trials.
- PMID:36482258 | 2023 | rct | clinical_hard | benefit | flags=preclinical_translation_risk
  title: The efficacy and safety of β-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial.
- PMID:33888596 | 2021 | rct | clinical_hard | benefit | flags=none
  title: Nicotinamide mononucleotide increases muscle insulin sensitivity in prediabetic women.

## 4) Trial Registry Audit
- None identified.

## 5) Preclinical Longevity Evidence
- PMID:36482258 | level=4 | rct | clinical_hard
  title: The efficacy and safety of β-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial.
- PMID:31685720 | level=5 | clinical_trial | clinical_intermediate
  title: Effect of oral administration of nicotinamide mononucleotide on clinical parameters and nicotinamide metabolite levels in healthy Japanese men.
- PMID:36797393 | level=4 | rct | clinical_hard
  title: Nicotinamide adenine dinucleotide metabolism and arterial stiffness after long-term nicotinamide mononucleotide supplementation: a randomized, double-blind, placebo-controlled trial.
- PMID:39548320 | level=5 | rct | clinical_hard
  title: Effect of nicotinamide riboside on airway inflammation in COPD: a randomized, placebo-controlled trial.
- PMID:36515353 | level=5 | rct | clinical_intermediate
  title: Oral nicotinamide riboside raises NAD+ and lowers biomarkers of neurodegenerative pathology in plasma extracellular vesicles enriched for neuronal origin.

## 6) Mechanistic Plausibility
- MP score: 23.0 / 30
- Hallmark tag count (observed in classified records): 6
- Interpretation: plausibility can support prioritization but cannot override weak human evidence.

## 7) Safety Summary
- None identified.

## 8) Confidence Score + Trace
- Overall score: 65.0 (C, moderate)
- CES: 70.0
- MP: 23.0
- Final confidence (trace): 65.0
- Penalties:
  - {'kind': 'quality', 'flag': 'observational_risk_confounding', 'count': 4, 'delta': -6.0}
  - {'kind': 'quality', 'flag': 'preclinical_translation_risk', 'count': 11, 'delta': -4.0}
  - {'kind': 'quality', 'flag': 'small_n_or_unknown', 'count': 11, 'delta': -8.0}
  - {'kind': 'quality', 'flag': 'not_completed', 'count': 16, 'delta': -8.0}
  - {'kind': 'quality', 'flag': 'no_registry_results', 'count': 26, 'delta': -6.0}
- Bonuses:
  - {'kind': 'consistency', 'reason': 'level1_plus_level2_present', 'delta': 4.0}
- Caps applied:
  - None identified.

## 9) Evidence Gaps + What Would Change the Score
- Missing evidence levels:
  - None identified.
- Missing endpoint classes:
  - None identified.
- Next best studies:
  - None identified.
- Registry/publication mismatch cautions:
  - None identified.

## 10) Limitations of this Automated Review
- Classification is metadata-driven and may miss details only available in full text.
- Registry/publication linkage can be incomplete for recently completed trials.
- The report reflects retrieved records only; hidden or unpublished studies may change conclusions.
- Records summarized: 20

```json
{
  "claim_context": null,
  "counts_by_endpoint": {
    "clinical_hard": 27,
    "clinical_intermediate": 6,
    "mechanistic_only": 19,
    "surrogate_biomarker": 9
  },
  "counts_by_source": {
    "clinicaltrials": 30,
    "pubmed": 31
  },
  "coverage_gaps": [],
  "evidence_pyramid": {
    "level_1": 6,
    "level_2": 38,
    "level_3": 6,
    "level_4": 7,
    "level_5": 4,
    "level_6": 0
  },
  "evidence_summary": {
    "confidence": "moderate",
    "label": "C",
    "notes": [],
    "score": 65.0
  },
  "gap_map": {
    "endpoint_counts": {
      "clinical_hard": 27,
      "clinical_intermediate": 6,
      "mechanistic_only": 19,
      "surrogate_biomarker": 9
    },
    "level_counts": {
      "1": 6,
      "2": 38,
      "3": 6,
      "4": 7,
      "5": 4
    },
    "mismatch_cautions": [],
    "missing_endpoints": [],
    "missing_levels": [],
    "next_best_studies": []
  },
  "intervention": {
    "label": "NAD+ Precursors (NMN / NR)"
  },
  "key_flags": [
    "indirect_endpoint",
    "preclinical_translation_risk"
  ],
  "optional_source_status": [],
  "preclinical_anchors": [
    {
      "citations": [
        {
          "doi": "10.1007/s11357-022-00705-1",
          "pmid": "36482258",
          "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1007/s11357-022-00705-1",
        "pmid": "36482258"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Animals",
          "Humans",
          "Middle Aged",
          "Nicotinamide Mononucleotide",
          "NAD",
          "Treatment Outcome",
          "Double-Blind Method",
          "Dietary Supplements"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Multicenter Study",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36482258",
      "study_type": "rct",
      "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1507/endocrj.EJ19-0313",
          "pmid": "31685720",
          "title": "Effect of oral administration of nicotinamide mononucleotide on clinical parameters and nicotinamide metabolite levels in healthy Japanese men."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1507/endocrj.EJ19-0313",
        "pmid": "31685720"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Administration, Oral",
          "Adult",
          "Bilirubin",
          "Blood Glucose",
          "Blood Pressure",
          "Body Temperature",
          "Chlorides",
          "Chromatography, Liquid",
          "Creatinine",
          "Diagnostic Techniques, Ophthalmological",
          "Dose-Response Relationship, Drug",
          "Electrocardiography",
          "Healthy Volunteers",
          "Heart Rate",
          "Humans",
          "Intraocular Pressure",
          "Japan",
          "Male",
          "Middle Aged",
          "Niacinamide",
          "Nicotinamide Mononucleotide",
          "Oxygen",
          "Pyridones",
          "Sleep",
          "Tandem Mass Spectrometry",
          "Visual Acuity"
        ],
        "pub_types": [
          "Clinical Trial",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:31685720",
      "study_type": "clinical_trial",
      "title": "Effect of oral administration of nicotinamide mononucleotide on clinical parameters and nicotinamide metabolite levels in healthy Japanese men.",
      "year": 2020
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41598-023-29787-3",
          "pmid": "36797393",
          "title": "Nicotinamide adenine dinucleotide metabolism and arterial stiffness after long-term nicotinamide mononucleotide supplementation: a randomized, double-blind, placebo-controlled trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "null",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s41598-023-29787-3",
        "pmid": "36797393"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Adult",
          "Animals",
          "Humans",
          "Middle Aged",
          "Dietary Supplements",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Pulse Wave Analysis",
          "Vascular Stiffness",
          "Double-Blind Method"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36797393",
      "study_type": "rct",
      "title": "Nicotinamide adenine dinucleotide metabolism and arterial stiffness after long-term nicotinamide mononucleotide supplementation: a randomized, double-blind, placebo-controlled trial.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-024-00758-1",
          "pmid": "39548320",
          "title": "Effect of nicotinamide riboside on airway inflammation in COPD: a randomized, placebo-controlled trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1038/s43587-024-00758-1",
        "pmid": "39548320"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "epigenetic_alterations",
          "cellular_senescence",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Pyridinium Compounds",
          "Niacinamide",
          "Pulmonary Disease, Chronic Obstructive",
          "Male",
          "Female",
          "Aged",
          "Double-Blind Method",
          "Middle Aged",
          "Interleukin-8",
          "Inflammation",
          "Sputum",
          "Interleukin-6",
          "NAD",
          "Treatment Outcome"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:39548320",
      "study_type": "rct",
      "title": "Effect of nicotinamide riboside on airway inflammation in COPD: a randomized, placebo-controlled trial.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1111/acel.13754",
          "pmid": "36515353",
          "title": "Oral nicotinamide riboside raises NAD+ and lowers biomarkers of neurodegenerative pathology in plasma extracellular vesicles enriched for neuronal origin."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1111/acel.13754",
        "pmid": "36515353"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Aged",
          "Humans",
          "Biomarkers",
          "Extracellular Vesicles",
          "Insulin",
          "NAD",
          "Neurodegenerative Diseases",
          "Niacinamide",
          "Pyridinium Compounds"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36515353",
      "study_type": "rct",
      "title": "Oral nicotinamide riboside raises NAD+ and lowers biomarkers of neurodegenerative pathology in plasma extracellular vesicles enriched for neuronal origin.",
      "year": 2023
    }
  ],
  "records": [
    {
      "citations": [
        {
          "doi": "10.1016/j.exger.2020.110831",
          "pmid": "31917996",
          "title": "NAD+ therapy in age-related degenerative disorders: A benefit/risk analysis."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1016/j.exger.2020.110831",
        "pmid": "31917996"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "cellular_senescence",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Aging",
          "Animals",
          "Humans",
          "Inflammation",
          "Mice",
          "NAD",
          "Neurodegenerative Diseases",
          "Niacinamide",
          "Nicotinamide Mononucleotide",
          "Oxidative Stress",
          "Pyridinium Compounds",
          "Rats",
          "Risk Assessment"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:31917996",
      "study_type": "meta_analysis",
      "title": "NAD+ therapy in age-related degenerative disorders: A benefit/risk analysis.",
      "year": 2020
    },
    {
      "citations": [
        {
          "doi": "10.2174/0113892010306242240808094303",
          "pmid": "39185644",
          "title": "Effects of Nicotinamide Mononucleotide Supplementation on Muscle and Liver Functions Among the Middle-aged and Elderly: A Systematic Review and Meta-analysis of Randomized Controlled Trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.2174/0113892010306242240808094303",
        "pmid": "39185644"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Humans",
          "Randomized Controlled Trials as Topic",
          "Nicotinamide Mononucleotide",
          "Aged",
          "Liver",
          "Middle Aged",
          "Dietary Supplements",
          "Muscle, Skeletal",
          "Aging"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review",
          "Meta-Analysis"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:39185644",
      "study_type": "meta_analysis",
      "title": "Effects of Nicotinamide Mononucleotide Supplementation on Muscle and Liver Functions Among the Middle-aged and Elderly: A Systematic Review and Meta-analysis of Randomized Controlled Trials.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.3389/fpubh.2023.1287421",
          "pmid": "37954044",
          "title": "Exercise training upregulates intracellular nicotinamide phosphoribosyltransferase expression in humans: a systematic review with meta-analysis."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "surrogate_biomarker",
      "evidence_level": 1,
      "ids": {
        "doi": "10.3389/fpubh.2023.1287421",
        "pmid": "37954044"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Aging",
          "Exercise",
          "Muscle, Skeletal",
          "NAD",
          "Nicotinamide Phosphoribosyltransferase"
        ],
        "pub_types": [
          "Meta-Analysis",
          "Systematic Review",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:37954044",
      "study_type": "meta_analysis",
      "title": "Exercise training upregulates intracellular nicotinamide phosphoribosyltransferase expression in humans: a systematic review with meta-analysis.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1007/s40520-022-02203-y",
          "pmid": "35920994",
          "title": "Impact of nutraceuticals and dietary supplements on mitochondria modifications in healthy aging: a systematic review of randomized controlled trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1007/s40520-022-02203-y",
        "pmid": "35920994"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Aged",
          "Healthy Aging",
          "Randomized Controlled Trials as Topic",
          "Dietary Supplements",
          "Fatty Acids, Omega-3",
          "Mitochondria"
        ],
        "pub_types": [
          "Systematic Review",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:35920994",
      "study_type": "meta_analysis",
      "title": "Impact of nutraceuticals and dietary supplements on mitochondria modifications in healthy aging: a systematic review of randomized controlled trials.",
      "year": 2022
    },
    {
      "citations": [
        {
          "doi": "10.1186/s12868-025-00937-9",
          "pmid": "40033213",
          "title": "A systematic review of the therapeutic potential of nicotinamide adenine dinucleotide precursors for cognitive diseases in preclinical rodent models."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1186/s12868-025-00937-9",
        "pmid": "40033213"
      },
      "metadata": {
        "hallmark_tags": [
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "NAD",
          "Disease Models, Animal",
          "Cognitive Dysfunction",
          "Humans",
          "Oxidative Stress",
          "Rats",
          "Mitochondria"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40033213",
      "study_type": "meta_analysis",
      "title": "A systematic review of the therapeutic potential of nicotinamide adenine dinucleotide precursors for cognitive diseases in preclinical rodent models.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1080/10408398.2024.2387324",
          "pmid": "39116016",
          "title": "Efficacy of oral nicotinamide mononucleotide supplementation on glucose and lipid metabolism for adults: a systematic review with meta-analysis on randomized controlled trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "null",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1080/10408398.2024.2387324",
        "pmid": "39116016"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Dietary Supplements",
          "Randomized Controlled Trials as Topic",
          "Blood Glucose",
          "Adult",
          "Lipid Metabolism",
          "Triglycerides",
          "NAD",
          "Administration, Oral"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review",
          "Meta-Analysis"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:39116016",
      "study_type": "meta_analysis",
      "title": "Efficacy of oral nicotinamide mononucleotide supplementation on glucose and lipid metabolism for adults: a systematic review with meta-analysis on randomized controlled trials.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1007/s11357-022-00705-1",
          "pmid": "36482258",
          "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1007/s11357-022-00705-1",
        "pmid": "36482258"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Animals",
          "Humans",
          "Middle Aged",
          "Nicotinamide Mononucleotide",
          "NAD",
          "Treatment Outcome",
          "Double-Blind Method",
          "Dietary Supplements"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Multicenter Study",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36482258",
      "study_type": "rct",
      "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1126/science.abe9985",
          "pmid": "33888596",
          "title": "Nicotinamide mononucleotide increases muscle insulin sensitivity in prediabetic women."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1126/science.abe9985",
        "pmid": "33888596"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Aged",
          "Body Composition",
          "Dietary Supplements",
          "Double-Blind Method",
          "Female",
          "Humans",
          "Insulin",
          "Insulin Resistance",
          "Middle Aged",
          "Mitochondria, Muscle",
          "Muscle, Skeletal",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Obesity",
          "Overweight",
          "Postmenopause",
          "Prediabetic State",
          "RNA-Seq",
          "Signal Transduction"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:33888596",
      "study_type": "rct",
      "title": "Nicotinamide mononucleotide increases muscle insulin sensitivity in prediabetic women.",
      "year": 2021
    },
    {
      "citations": [
        {
          "doi": "10.1210/clinem/dgad027",
          "pmid": "36740954",
          "title": "Nicotinamide Adenine Dinucleotide Augmentation in Overweight or Obese Middle-Aged and Older Adults: A Physiologic Study."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1210/clinem/dgad027",
        "pmid": "36740954"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Middle Aged",
          "Humans",
          "Aged",
          "Overweight",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Insulin Resistance",
          "Obesity",
          "Body Weight",
          "Cholesterol"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:36740954",
      "study_type": "rct",
      "title": "Nicotinamide Adenine Dinucleotide Augmentation in Overweight or Obese Middle-Aged and Older Adults: A Physiologic Study.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41467-018-03421-7",
          "pmid": "29599478",
          "title": "Chronic\u00a0nicotinamide riboside supplementation is well-tolerated and elevates NAD+ in healthy middle-aged and older adults."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1038/s41467-018-03421-7",
        "pmid": "29599478"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Aged",
          "Blood Pressure",
          "Caloric Restriction",
          "Double-Blind Method",
          "Female",
          "Humans",
          "Male",
          "Middle Aged",
          "NAD",
          "Niacinamide",
          "Pyridinium Compounds",
          "Vascular Stiffness"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:29599478",
      "study_type": "rct",
      "title": "Chronic\u00a0nicotinamide riboside supplementation is well-tolerated and elevates NAD+ in healthy middle-aged and older adults.",
      "year": 2018
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.celrep.2019.07.043",
          "pmid": "31412242",
          "title": "Nicotinamide Riboside Augments the Aged Human Skeletal Muscle NAD+ Metabolome and Induces Transcriptomic and Anti-inflammatory Signatures."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "surrogate_biomarker",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1016/j.celrep.2019.07.043",
        "pmid": "31412242"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Aged",
          "Aged, 80 and over",
          "Aging",
          "Anti-Inflammatory Agents",
          "Cross-Sectional Studies",
          "Cytokines",
          "Double-Blind Method",
          "Humans",
          "Male",
          "Metabolome",
          "Muscle, Skeletal",
          "NAD",
          "Niacinamide",
          "Pyridinium Compounds",
          "Transcriptome"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:31412242",
      "study_type": "rct",
      "title": "Nicotinamide Riboside Augments the Aged Human Skeletal Muscle NAD+ Metabolome and Induces Transcriptomic and Anti-inflammatory Signatures.",
      "year": 2019
    },
    {
      "citations": [
        {
          "doi": "10.1507/endocrj.EJ19-0313",
          "pmid": "31685720",
          "title": "Effect of oral administration of nicotinamide mononucleotide on clinical parameters and nicotinamide metabolite levels in healthy Japanese men."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1507/endocrj.EJ19-0313",
        "pmid": "31685720"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Administration, Oral",
          "Adult",
          "Bilirubin",
          "Blood Glucose",
          "Blood Pressure",
          "Body Temperature",
          "Chlorides",
          "Chromatography, Liquid",
          "Creatinine",
          "Diagnostic Techniques, Ophthalmological",
          "Dose-Response Relationship, Drug",
          "Electrocardiography",
          "Healthy Volunteers",
          "Heart Rate",
          "Humans",
          "Intraocular Pressure",
          "Japan",
          "Male",
          "Middle Aged",
          "Niacinamide",
          "Nicotinamide Mononucleotide",
          "Oxygen",
          "Pyridones",
          "Sleep",
          "Tandem Mass Spectrometry",
          "Visual Acuity"
        ],
        "pub_types": [
          "Clinical Trial",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:31685720",
      "study_type": "clinical_trial",
      "title": "Effect of oral administration of nicotinamide mononucleotide on clinical parameters and nicotinamide metabolite levels in healthy Japanese men.",
      "year": 2020
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41598-023-29787-3",
          "pmid": "36797393",
          "title": "Nicotinamide adenine dinucleotide metabolism and arterial stiffness after long-term nicotinamide mononucleotide supplementation: a randomized, double-blind, placebo-controlled trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "null",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s41598-023-29787-3",
        "pmid": "36797393"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Adult",
          "Animals",
          "Humans",
          "Middle Aged",
          "Dietary Supplements",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Pulse Wave Analysis",
          "Vascular Stiffness",
          "Double-Blind Method"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36797393",
      "study_type": "rct",
      "title": "Nicotinamide adenine dinucleotide metabolism and arterial stiffness after long-term nicotinamide mononucleotide supplementation: a randomized, double-blind, placebo-controlled trial.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-024-00758-1",
          "pmid": "39548320",
          "title": "Effect of nicotinamide riboside on airway inflammation in COPD: a randomized, placebo-controlled trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1038/s43587-024-00758-1",
        "pmid": "39548320"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "epigenetic_alterations",
          "cellular_senescence",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Pyridinium Compounds",
          "Niacinamide",
          "Pulmonary Disease, Chronic Obstructive",
          "Male",
          "Female",
          "Aged",
          "Double-Blind Method",
          "Middle Aged",
          "Interleukin-8",
          "Inflammation",
          "Sputum",
          "Interleukin-6",
          "NAD",
          "Treatment Outcome"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:39548320",
      "study_type": "rct",
      "title": "Effect of nicotinamide riboside on airway inflammation in COPD: a randomized, placebo-controlled trial.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1111/acel.13754",
          "pmid": "36515353",
          "title": "Oral nicotinamide riboside raises NAD+ and lowers biomarkers of neurodegenerative pathology in plasma extracellular vesicles enriched for neuronal origin."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1111/acel.13754",
        "pmid": "36515353"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Aged",
          "Humans",
          "Biomarkers",
          "Extracellular Vesicles",
          "Insulin",
          "NAD",
          "Neurodegenerative Diseases",
          "Niacinamide",
          "Pyridinium Compounds"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36515353",
      "study_type": "rct",
      "title": "Oral nicotinamide riboside raises NAD+ and lowers biomarkers of neurodegenerative pathology in plasma extracellular vesicles enriched for neuronal origin.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1172/jci.insight.158314",
          "pmid": "35998039",
          "title": "A randomized placebo-controlled trial of nicotinamide riboside and pterostilbene supplementation in experimental muscle injury in elderly individuals."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1172/jci.insight.158314",
        "pmid": "35998039"
      },
      "metadata": {
        "hallmark_tags": [
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Aged",
          "Creatine Kinase, MM Form",
          "Dietary Supplements",
          "Humans",
          "Muscle, Skeletal",
          "Muscular Diseases",
          "Myoglobin",
          "Myosin Heavy Chains",
          "Niacinamide",
          "Pyridinium Compounds",
          "Stilbenes"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:35998039",
      "study_type": "rct",
      "title": "A randomized placebo-controlled trial of nicotinamide riboside and pterostilbene supplementation in experimental muscle injury in elderly individuals.",
      "year": 2022
    },
    {
      "citations": [
        {
          "doi": "10.1111/ggi.14513",
          "pmid": "36443648",
          "title": "Effects of nicotinamide mononucleotide on older patients with diabetes and impaired physical performance: A prospective, placebo-controlled, double-blind study."
        }
      ],
      "directness_flags": [],
      "effect_direction": "null",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1111/ggi.14513",
        "pmid": "36443648"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Male",
          "Diabetes Mellitus",
          "Double-Blind Method",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Prospective Studies",
          "Humans",
          "Aged",
          "Hand Strength",
          "Walking Speed"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:36443648",
      "study_type": "rct",
      "title": "Effects of nicotinamide mononucleotide on older patients with diabetes and impaired physical performance: A prospective, placebo-controlled, double-blind study.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1111/acel.70093",
          "pmid": "40459998",
          "title": "Nicotinamide Riboside Supplementation Benefits in Patients With Werner Syndrome: A Double-Blind Randomized Crossover Placebo-Controlled Trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1111/acel.70093",
        "pmid": "40459998"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Niacinamide",
          "Double-Blind Method",
          "Werner Syndrome",
          "Male",
          "Female",
          "Cross-Over Studies",
          "Dietary Supplements",
          "Middle Aged",
          "Pyridinium Compounds",
          "Adult"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40459998",
      "study_type": "rct",
      "title": "Nicotinamide Riboside Supplementation Benefits in Patients With Werner Syndrome: A Double-Blind Randomized Crossover Placebo-Controlled Trial.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1093/gerona/glac049",
          "pmid": "35182418",
          "title": "MIB-626, an Oral Formulation of a Microcrystalline Unique Polymorph of \u03b2-Nicotinamide Mononucleotide, Increases Circulating Nicotinamide Adenine Dinucleotide and its Metabolome in Middle-Aged and Older Adults."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1093/gerona/glac049",
        "pmid": "35182418"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Middle Aged",
          "Aged",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Metabolome",
          "Mass Spectrometry",
          "Body Mass Index"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:35182418",
      "study_type": "rct",
      "title": "MIB-626, an Oral Formulation of a Microcrystalline Unique Polymorph of \u03b2-Nicotinamide Mononucleotide, Increases Circulating Nicotinamide Adenine Dinucleotide and its Metabolome in Middle-Aged and Older Adults.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.jpet.2025.103607",
          "pmid": "40479886",
          "title": "Effects of NAD+ supplementation with oral nicotinamide riboside on vascular health and cognitive function in older adults with peripheral artery disease: Results from a pilot 4-week open-label clinical trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1016/j.jpet.2025.103607",
        "pmid": "40479886"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Pilot Projects",
          "Niacinamide",
          "Aged",
          "Male",
          "Female",
          "Cognition",
          "NAD",
          "Peripheral Arterial Disease",
          "Dietary Supplements",
          "Pyridinium Compounds",
          "Administration, Oral",
          "Oxidative Stress",
          "Middle Aged",
          "Mitochondria",
          "Aged, 80 and over",
          "Endothelium, Vascular"
        ],
        "pub_types": [
          "Journal Article",
          "Clinical Trial"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40479886",
      "study_type": "clinical_trial",
      "title": "Effects of NAD+ supplementation with oral nicotinamide riboside on vascular health and cognitive function in older adults with peripheral artery disease: Results from a pilot 4-week open-label clinical trial.",
      "year": 2025
    }
  ],
  "scoring_trace": {
    "bonuses": [
      {
        "delta": 4.0,
        "kind": "consistency",
        "reason": "level1_plus_level2_present"
      }
    ],
    "caps_applied": [],
    "ces": 70.0,
    "components": {
      "ces_components": {
        "level_1": 40.0,
        "level_2": 28.0,
        "level_3": 16.0,
        "level_4": 8.0,
        "level_5": 4.0
      },
      "consistency_bonus": 4.0,
      "endpoint_counts": {
        "clinical_hard": 27,
        "clinical_intermediate": 6,
        "mechanistic_only": 19,
        "surrogate_biomarker": 9
      },
      "hallmark_tag_count": 6,
      "human_count": 56,
      "level_counts": {
        "1": 6,
        "2": 38,
        "3": 6,
        "4": 7,
        "5": 4
      },
      "quality_flags": {
        "no_registry_results": 26,
        "not_completed": 16,
        "observational_risk_confounding": 4,
        "preclinical_translation_risk": 11,
        "small_n_or_unknown": 11
      },
      "quality_penalty": 32.0
    },
    "final_confidence": 65.0,
    "mp": 23.0,
    "penalties": [
      {
        "count": 4,
        "delta": -6.0,
        "flag": "observational_risk_confounding",
        "kind": "quality"
      },
      {
        "count": 11,
        "delta": -4.0,
        "flag": "preclinical_translation_risk",
        "kind": "quality"
      },
      {
        "count": 11,
        "delta": -8.0,
        "flag": "small_n_or_unknown",
        "kind": "quality"
      },
      {
        "count": 16,
        "delta": -8.0,
        "flag": "not_completed",
        "kind": "quality"
      },
      {
        "count": 26,
        "delta": -6.0,
        "flag": "no_registry_results",
        "kind": "quality"
      }
    ]
  },
  "top_human_studies": [
    {
      "citations": [
        {
          "doi": "10.1016/j.exger.2020.110831",
          "pmid": "31917996",
          "title": "NAD+ therapy in age-related degenerative disorders: A benefit/risk analysis."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1016/j.exger.2020.110831",
        "pmid": "31917996"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "cellular_senescence",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Aging",
          "Animals",
          "Humans",
          "Inflammation",
          "Mice",
          "NAD",
          "Neurodegenerative Diseases",
          "Niacinamide",
          "Nicotinamide Mononucleotide",
          "Oxidative Stress",
          "Pyridinium Compounds",
          "Rats",
          "Risk Assessment"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:31917996",
      "study_type": "meta_analysis",
      "title": "NAD+ therapy in age-related degenerative disorders: A benefit/risk analysis.",
      "year": 2020
    },
    {
      "citations": [
        {
          "doi": "10.2174/0113892010306242240808094303",
          "pmid": "39185644",
          "title": "Effects of Nicotinamide Mononucleotide Supplementation on Muscle and Liver Functions Among the Middle-aged and Elderly: A Systematic Review and Meta-analysis of Randomized Controlled Trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.2174/0113892010306242240808094303",
        "pmid": "39185644"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Humans",
          "Randomized Controlled Trials as Topic",
          "Nicotinamide Mononucleotide",
          "Aged",
          "Liver",
          "Middle Aged",
          "Dietary Supplements",
          "Muscle, Skeletal",
          "Aging"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review",
          "Meta-Analysis"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:39185644",
      "study_type": "meta_analysis",
      "title": "Effects of Nicotinamide Mononucleotide Supplementation on Muscle and Liver Functions Among the Middle-aged and Elderly: A Systematic Review and Meta-analysis of Randomized Controlled Trials.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.3389/fpubh.2023.1287421",
          "pmid": "37954044",
          "title": "Exercise training upregulates intracellular nicotinamide phosphoribosyltransferase expression in humans: a systematic review with meta-analysis."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "surrogate_biomarker",
      "evidence_level": 1,
      "ids": {
        "doi": "10.3389/fpubh.2023.1287421",
        "pmid": "37954044"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Aging",
          "Exercise",
          "Muscle, Skeletal",
          "NAD",
          "Nicotinamide Phosphoribosyltransferase"
        ],
        "pub_types": [
          "Meta-Analysis",
          "Systematic Review",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:37954044",
      "study_type": "meta_analysis",
      "title": "Exercise training upregulates intracellular nicotinamide phosphoribosyltransferase expression in humans: a systematic review with meta-analysis.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1007/s40520-022-02203-y",
          "pmid": "35920994",
          "title": "Impact of nutraceuticals and dietary supplements on mitochondria modifications in healthy aging: a systematic review of randomized controlled trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1007/s40520-022-02203-y",
        "pmid": "35920994"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Aged",
          "Healthy Aging",
          "Randomized Controlled Trials as Topic",
          "Dietary Supplements",
          "Fatty Acids, Omega-3",
          "Mitochondria"
        ],
        "pub_types": [
          "Systematic Review",
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:35920994",
      "study_type": "meta_analysis",
      "title": "Impact of nutraceuticals and dietary supplements on mitochondria modifications in healthy aging: a systematic review of randomized controlled trials.",
      "year": 2022
    },
    {
      "citations": [
        {
          "doi": "10.1186/s12868-025-00937-9",
          "pmid": "40033213",
          "title": "A systematic review of the therapeutic potential of nicotinamide adenine dinucleotide precursors for cognitive diseases in preclinical rodent models."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1186/s12868-025-00937-9",
        "pmid": "40033213"
      },
      "metadata": {
        "hallmark_tags": [
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "NAD",
          "Disease Models, Animal",
          "Cognitive Dysfunction",
          "Humans",
          "Oxidative Stress",
          "Rats",
          "Mitochondria"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40033213",
      "study_type": "meta_analysis",
      "title": "A systematic review of the therapeutic potential of nicotinamide adenine dinucleotide precursors for cognitive diseases in preclinical rodent models.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1080/10408398.2024.2387324",
          "pmid": "39116016",
          "title": "Efficacy of oral nicotinamide mononucleotide supplementation on glucose and lipid metabolism for adults: a systematic review with meta-analysis on randomized controlled trials."
        }
      ],
      "directness_flags": [],
      "effect_direction": "null",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1080/10408398.2024.2387324",
        "pmid": "39116016"
      },
      "metadata": {
        "hallmark_tags": [],
        "mesh_terms": [
          "Humans",
          "Dietary Supplements",
          "Randomized Controlled Trials as Topic",
          "Blood Glucose",
          "Adult",
          "Lipid Metabolism",
          "Triglycerides",
          "NAD",
          "Administration, Oral"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review",
          "Meta-Analysis"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:39116016",
      "study_type": "meta_analysis",
      "title": "Efficacy of oral nicotinamide mononucleotide supplementation on glucose and lipid metabolism for adults: a systematic review with meta-analysis on randomized controlled trials.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1007/s11357-022-00705-1",
          "pmid": "36482258",
          "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1007/s11357-022-00705-1",
        "pmid": "36482258"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Animals",
          "Humans",
          "Middle Aged",
          "Nicotinamide Mononucleotide",
          "NAD",
          "Treatment Outcome",
          "Double-Blind Method",
          "Dietary Supplements"
        ],
        "pub_types": [
          "Randomized Controlled Trial",
          "Multicenter Study",
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:36482258",
      "study_type": "rct",
      "title": "The efficacy and safety of \u03b2-nicotinamide mononucleotide (NMN) supplementation in healthy middle-aged adults: a randomized, multicenter, double-blind, placebo-controlled, parallel-group, dose-dependent clinical trial.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1126/science.abe9985",
          "pmid": "33888596",
          "title": "Nicotinamide mononucleotide increases muscle insulin sensitivity in prediabetic women."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 2,
      "ids": {
        "doi": "10.1126/science.abe9985",
        "pmid": "33888596"
      },
      "metadata": {
        "hallmark_tags": [
          "nutrient_sensing"
        ],
        "mesh_terms": [
          "Aged",
          "Body Composition",
          "Dietary Supplements",
          "Double-Blind Method",
          "Female",
          "Humans",
          "Insulin",
          "Insulin Resistance",
          "Middle Aged",
          "Mitochondria, Muscle",
          "Muscle, Skeletal",
          "NAD",
          "Nicotinamide Mononucleotide",
          "Obesity",
          "Overweight",
          "Postmenopause",
          "Prediabetic State",
          "RNA-Seq",
          "Signal Transduction"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:33888596",
      "study_type": "rct",
      "title": "Nicotinamide mononucleotide increases muscle insulin sensitivity in prediabetic women.",
      "year": 2021
    }
  ],
  "trial_registry_rows": []
}
```
