# Evidence Report: Epigenetic reprogramming

## 1) Intervention Identity
- Type: unknown
- Pivot: None identified
- Query: Epigenetic reprogramming
- Population: generally healthy adults / older adults
- Outcome: clinically relevant ageing outcomes / healthspan
- Ambiguity notes:
  - None identified.
- Directness warnings:
  - None identified.

## 2) Evidence Pyramid
- Level 1 (systematic/meta): 2
- Level 2 (human interventional): 3
- Level 3 (human observational): 26
- Level 4 (animal in vivo): 8
- Level 5 (in vitro): 2
- Level 6 (in silico): 0

## 3) Key Human Evidence
- PMID:41534275 | 2026 | observational | surrogate_biomarker | benefit | flags=observational_risk_confounding, indirect_endpoint
  title: Molecular basis for the early life sensitization of the brain to ultrafine carbonaceous particles: a study of the brain proteome, telomeres, and epigenetic modelling.
- PMID:40506775 | 2025 | observational | clinical_hard | harm | flags=observational_risk_confounding
  title: Exercise orchestrates systemic metabolic and neuroimmune homeostasis via the brain-muscle-liver axis to slow down aging and neurodegeneration: a narrative review.
- PMID:39259812 | 2024 | in_vitro | clinical_intermediate | benefit | flags=observational_risk_confounding
  title: Targeted partial reprogramming of age-associated cell states improves markers of health in mouse models of aging.
- PMID:40642939 | 2025 | observational | clinical_hard | benefit | flags=observational_risk_confounding
  title: Biomarkers of aging as it relates osteoarthritis: we can't improve what we can't measure.
- PMID:38332583 | 2024 | in_vitro | clinical_intermediate | benefit | flags=observational_risk_confounding
  title: M2 macrophages secrete glutamate-containing extracellular vesicles to alleviate osteoporosis by reshaping osteoclast precursor fate.
- PMID:40815481 | 2025 | meta_analysis | mechanistic_only | benefit | flags=indirect_endpoint
  title: Integration of Vascular Smooth Muscle Cell Phenotypic Switching and Senescence.
- PMID:33049666 | 2021 | in_vitro | clinical_intermediate | benefit | flags=observational_risk_confounding
  title: From genoprotection to rejuvenation.
- PMID:38102454 | 2024 | in_vitro | mechanistic_only | benefit | flags=observational_risk_confounding, indirect_endpoint
  title: Mechanisms, pathways and strategies for rejuvenation through epigenetic reprogramming.

## 4) Trial Registry Audit
- None identified.

## 5) Preclinical Longevity Evidence
- PMID:23699511 | level=4 | rct | mechanistic_only
  title: Paternal stress exposure alters sperm microRNA content and reprograms offspring HPA stress axis regulation.
- PMID:34035273 | level=4 | observational | mechanistic_only
  title: In vivo partial reprogramming of myofibers promotes muscle regeneration by remodeling the stem cell niche.
- PMID:38371924 | level=5 | in_vitro | mechanistic_only
  title: Next-generation direct reprogramming.
- PMID:38102202 | level=5 | in_vitro | clinical_hard
  title: The Information Theory of Aging.
- PMID:38553564 | level=4 | in_vitro | clinical_hard
  title: Restoration of neuronal progenitors by partial reprogramming in the aged neurogenic niche.
- PMID:39053462 | level=4 | observational | clinical_hard
  title: Innate immune training restores pro-reparative myeloid functions to promote remyelination in the aged central nervous system.

## 6) Mechanistic Plausibility
- MP score: 27.0 / 30
- Hallmark tag count (observed in classified records): 8
- Interpretation: plausibility can support prioritization but cannot override weak human evidence.

## 7) Safety Summary
- None identified.

## 8) Confidence Score + Trace
- Overall score: 69.0 (C, moderate)
- CES: 70.0
- MP: 27.0
- Final confidence (trace): 69.0
- Penalties:
  - {'kind': 'quality', 'flag': 'observational_risk_confounding', 'count': 19, 'delta': -6.0}
  - {'kind': 'quality', 'flag': 'preclinical_translation_risk', 'count': 10, 'delta': -4.0}
  - {'kind': 'quality', 'flag': 'small_n_or_unknown', 'count': 5, 'delta': -8.0}
  - {'kind': 'quality', 'flag': 'not_completed', 'count': 6, 'delta': -8.0}
  - {'kind': 'quality', 'flag': 'no_registry_results', 'count': 10, 'delta': -6.0}
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
  "claim_context": {
    "ambiguity_warnings": [],
    "ask_clarify": false,
    "claim_mode": "explicit",
    "comparator": "placebo / standard of care / no intervention",
    "directness_warnings": [],
    "intervention": "",
    "outcome": "clinically relevant ageing outcomes / healthspan",
    "population": "generally healthy adults / older adults",
    "query": ""
  },
  "counts_by_endpoint": {
    "clinical_hard": 16,
    "clinical_intermediate": 4,
    "mechanistic_only": 19,
    "surrogate_biomarker": 2
  },
  "counts_by_source": {
    "clinicaltrials": 10,
    "pubmed": 31
  },
  "coverage_gaps": [],
  "evidence_pyramid": {
    "level_1": 2,
    "level_2": 3,
    "level_3": 26,
    "level_4": 8,
    "level_5": 2,
    "level_6": 0
  },
  "evidence_summary": {
    "confidence": "moderate",
    "label": "C",
    "notes": [],
    "score": 69.0
  },
  "gap_map": {
    "endpoint_counts": {
      "clinical_hard": 16,
      "clinical_intermediate": 4,
      "mechanistic_only": 19,
      "surrogate_biomarker": 2
    },
    "level_counts": {
      "1": 2,
      "2": 3,
      "3": 26,
      "4": 8,
      "5": 2
    },
    "mismatch_cautions": [],
    "missing_endpoints": [],
    "missing_levels": [],
    "next_best_studies": []
  },
  "intervention": {
    "label": "Epigenetic reprogramming"
  },
  "key_flags": [
    "observational_risk_confounding",
    "indirect_endpoint",
    "preclinical_translation_risk"
  ],
  "optional_source_status": [],
  "preclinical_anchors": [
    {
      "citations": [
        {
          "doi": "10.1523/JNEUROSCI.0914-13.2013",
          "pmid": "23699511",
          "title": "Paternal stress exposure alters sperm microRNA content and reprograms offspring HPA stress axis regulation."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1523/JNEUROSCI.0914-13.2013",
        "pmid": "23699511"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Acoustic Stimulation",
          "Adaptation, Ocular",
          "Analysis of Variance",
          "Animals",
          "Animals, Newborn",
          "Citalopram",
          "Disease Models, Animal",
          "Female",
          "Gene Expression Regulation",
          "Hindlimb Suspension",
          "Hypothalamo-Hypophyseal System",
          "Male",
          "Maze Learning",
          "Mice",
          "Mice, Inbred C57BL",
          "MicroRNAs",
          "Pituitary-Adrenal System",
          "Pregnancy",
          "Prenatal Exposure Delayed Effects",
          "Reflex, Startle",
          "Selective Serotonin Reuptake Inhibitors",
          "Sex Factors",
          "Spermatozoa",
          "Stress, Psychological"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:23699511",
      "study_type": "rct",
      "title": "Paternal stress exposure alters sperm microRNA content and reprograms offspring HPA stress axis regulation.",
      "year": 2013
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41467-021-23353-z",
          "pmid": "34035273",
          "title": "In vivo partial reprogramming of myofibers promotes muscle regeneration by remodeling the stem cell niche."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "unknown",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s41467-021-23353-z",
        "pmid": "34035273"
      },
      "metadata": {
        "hallmark_tags": [
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Animals",
          "Cell Differentiation",
          "Cells, Cultured",
          "Cellular Reprogramming",
          "Female",
          "Gene Expression",
          "Kruppel-Like Factor 4",
          "Kruppel-Like Transcription Factors",
          "Mice, Transgenic",
          "Myofibrils",
          "Octamer Transcription Factor-3",
          "Proto-Oncogene Proteins c-myc",
          "Regeneration",
          "SOXB1 Transcription Factors",
          "Satellite Cells, Skeletal Muscle",
          "Stem Cell Niche",
          "Wnt4 Protein"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't",
          "Research Support, U.S. Gov't, Non-P.H.S."
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:34035273",
      "study_type": "observational",
      "title": "In vivo partial reprogramming of myofibers promotes muscle regeneration by remodeling the stem cell niche.",
      "year": 2021
    },
    {
      "citations": [
        {
          "doi": "10.3389/fcell.2024.1343106",
          "pmid": "38371924",
          "title": "Next-generation direct reprogramming."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "unknown",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 5,
      "ids": {
        "doi": "10.3389/fcell.2024.1343106",
        "pmid": "38371924"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "cell",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38371924",
      "study_type": "in_vitro",
      "title": "Next-generation direct reprogramming.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-023-00527-6",
          "pmid": "38102202",
          "title": "The Information Theory of Aging."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1038/s43587-023-00527-6",
        "pmid": "38102202"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Epigenesis, Genetic",
          "DNA Methylation",
          "Information Theory",
          "Histones"
        ],
        "pub_types": [
          "Journal Article",
          "Review",
          "Research Support, Non-U.S. Gov't",
          "Research Support, N.I.H., Extramural"
        ]
      },
      "population_class": "cell",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38102202",
      "study_type": "in_vitro",
      "title": "The Information Theory of Aging.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-024-00594-3",
          "pmid": "38553564",
          "title": "Restoration of neuronal progenitors by partial reprogramming in the aged neurogenic niche."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s43587-024-00594-3",
        "pmid": "38553564"
      },
      "metadata": {
        "hallmark_tags": [
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Mice",
          "Animals",
          "Neurons",
          "Neurogenesis",
          "Neural Stem Cells",
          "Cell Differentiation",
          "Cellular Reprogramming"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38553564",
      "study_type": "in_vitro",
      "title": "Restoration of neuronal progenitors by partial reprogramming in the aged neurogenic niche.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.immuni.2024.07.001",
          "pmid": "39053462",
          "title": "Innate immune training restores pro-reparative myeloid functions to promote remyelination in the aged central nervous system."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1016/j.immuni.2024.07.001",
        "pmid": "39053462"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "Immunity, Innate",
          "Mice",
          "Aging",
          "Remyelination",
          "Microglia",
          "Myeloid Cells",
          "Central Nervous System",
          "Mice, Inbred C57BL",
          "Myelin Sheath",
          "Epigenesis, Genetic",
          "Demyelinating Diseases",
          "Disease Models, Animal"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:39053462",
      "study_type": "observational",
      "title": "Innate immune training restores pro-reparative myeloid functions to promote remyelination in the aged central nervous system.",
      "year": 2024
    }
  ],
  "records": [
    {
      "citations": [
        {
          "doi": "10.1016/j.envint.2026.110058",
          "pmid": "41534275",
          "title": "Molecular basis for the early life sensitization of the brain to ultrafine carbonaceous particles: a study of the brain proteome, telomeres, and epigenetic modelling."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "surrogate_biomarker",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1016/j.envint.2026.110058",
        "pmid": "41534275"
      },
      "metadata": {
        "hallmark_tags": [
          "telomere_attrition",
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Animals",
          "Epigenesis, Genetic",
          "Particulate Matter",
          "Brain",
          "Mice",
          "Mice, Inbred C57BL",
          "Female",
          "Proteome",
          "Telomere",
          "Humans",
          "Pregnancy",
          "Air Pollutants",
          "Male",
          "Prenatal Exposure Delayed Effects",
          "Carbon"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:41534275",
      "study_type": "observational",
      "title": "Molecular basis for the early life sensitization of the brain to ultrafine carbonaceous particles: a study of the brain proteome, telomeres, and epigenetic modelling.",
      "year": 2026
    },
    {
      "citations": [
        {
          "doi": "10.1186/s40001-025-02751-9",
          "pmid": "40506775",
          "title": "Exercise orchestrates systemic metabolic and neuroimmune homeostasis via the brain-muscle-liver axis to slow down aging and neurodegeneration: a narrative review."
        }
      ],
      "directness_flags": [],
      "effect_direction": "harm",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1186/s40001-025-02751-9",
        "pmid": "40506775"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "proteostasis",
          "nutrient_sensing",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Aging",
          "Exercise",
          "Brain",
          "Neurodegenerative Diseases",
          "Homeostasis",
          "Liver",
          "Muscle, Skeletal",
          "Animals"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:40506775",
      "study_type": "observational",
      "title": "Exercise orchestrates systemic metabolic and neuroimmune homeostasis via the brain-muscle-liver axis to slow down aging and neurodegeneration: a narrative review.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1126/scitranslmed.adg1777",
          "pmid": "39259812",
          "title": "Targeted partial reprogramming of age-associated cell states improves markers of health in mouse models of aging."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1126/scitranslmed.adg1777",
        "pmid": "39259812"
      },
      "metadata": {
        "hallmark_tags": [
          "cellular_senescence",
          "stem_cell_exhaustion",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "Kruppel-Like Factor 4",
          "Aging",
          "Cellular Reprogramming",
          "Disease Models, Animal",
          "Cellular Senescence",
          "Mice",
          "Humans",
          "Cyclin-Dependent Kinase Inhibitor p16",
          "Biomarkers",
          "Progeria",
          "Dependovirus",
          "Promoter Regions, Genetic"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:39259812",
      "study_type": "in_vitro",
      "title": "Targeted partial reprogramming of age-associated cell states improves markers of health in mouse models of aging.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1080/03008207.2025.2528792",
          "pmid": "40642939",
          "title": "Biomarkers of aging as it relates osteoarthritis: we can't improve what we can't measure."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1080/03008207.2025.2528792",
        "pmid": "40642939"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "epigenetic_alterations",
          "cellular_senescence"
        ],
        "mesh_terms": [
          "Humans",
          "Osteoarthritis",
          "Biomarkers",
          "Aging",
          "Animals",
          "Chondrocytes"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:40642939",
      "study_type": "observational",
      "title": "Biomarkers of aging as it relates osteoarthritis: we can't improve what we can't measure.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.ymthe.2024.02.005",
          "pmid": "38332583",
          "title": "M2 macrophages secrete glutamate-containing extracellular vesicles to alleviate osteoporosis by reshaping osteoclast precursor fate."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1016/j.ymthe.2024.02.005",
        "pmid": "38332583"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Osteoclasts",
          "Glutamic Acid",
          "Macrophages",
          "Osteoporosis",
          "Extracellular Vesicles"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:38332583",
      "study_type": "in_vitro",
      "title": "M2 macrophages secrete glutamate-containing extracellular vesicles to alleviate osteoporosis by reshaping osteoclast precursor fate.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1097/FJC.0000000000001752",
          "pmid": "40815481",
          "title": "Integration of Vascular Smooth Muscle Cell Phenotypic Switching and Senescence."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1097/FJC.0000000000001752",
        "pmid": "40815481"
      },
      "metadata": {
        "hallmark_tags": [
          "cellular_senescence",
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Cellular Senescence",
          "Humans",
          "Muscle, Smooth, Vascular",
          "Animals",
          "Myocytes, Smooth Muscle",
          "Cell Transdifferentiation",
          "Phenotype",
          "Cardiovascular Diseases",
          "Signal Transduction",
          "Cell Plasticity",
          "Senescence-Associated Secretory Phenotype",
          "Vascular Calcification",
          "Cell Proliferation"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40815481",
      "study_type": "meta_analysis",
      "title": "Integration of Vascular Smooth Muscle Cell Phenotypic Switching and Senescence.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.2741/4890",
          "pmid": "33049666",
          "title": "From genoprotection to rejuvenation."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.2741/4890",
        "pmid": "33049666"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "nutrient_sensing",
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Aging",
          "Animals",
          "Epigenesis, Genetic",
          "Humans",
          "Longevity",
          "Rejuvenation",
          "Signal Transduction"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:33049666",
      "study_type": "in_vitro",
      "title": "From genoprotection to rejuvenation.",
      "year": 2021
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-023-00539-2",
          "pmid": "38102454",
          "title": "Mechanisms, pathways and strategies for rejuvenation through epigenetic reprogramming."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1038/s43587-023-00539-2",
        "pmid": "38102454"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Humans",
          "Animals",
          "Mice",
          "Rejuvenation",
          "Aging",
          "Cellular Reprogramming",
          "Induced Pluripotent Stem Cells",
          "Epigenesis, Genetic"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:38102454",
      "study_type": "in_vitro",
      "title": "Mechanisms, pathways and strategies for rejuvenation through epigenetic reprogramming.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.3390/nu15214699",
          "pmid": "37960352",
          "title": "Uncovering the Hidden Dangers and Molecular Mechanisms of Excess Folate: A Narrative Review."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 3,
      "ids": {
        "doi": "10.3390/nu15214699",
        "pmid": "37960352"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Pregnancy",
          "Female",
          "Humans",
          "Folic Acid",
          "Dietary Supplements",
          "Vitamin B 12",
          "Folic Acid Deficiency",
          "DNA Methylation"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:37960352",
      "study_type": "observational",
      "title": "Uncovering the Hidden Dangers and Molecular Mechanisms of Excess Folate: A Narrative Review.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1523/JNEUROSCI.0914-13.2013",
          "pmid": "23699511",
          "title": "Paternal stress exposure alters sperm microRNA content and reprograms offspring HPA stress axis regulation."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1523/JNEUROSCI.0914-13.2013",
        "pmid": "23699511"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Acoustic Stimulation",
          "Adaptation, Ocular",
          "Analysis of Variance",
          "Animals",
          "Animals, Newborn",
          "Citalopram",
          "Disease Models, Animal",
          "Female",
          "Gene Expression Regulation",
          "Hindlimb Suspension",
          "Hypothalamo-Hypophyseal System",
          "Male",
          "Maze Learning",
          "Mice",
          "Mice, Inbred C57BL",
          "MicroRNAs",
          "Pituitary-Adrenal System",
          "Pregnancy",
          "Prenatal Exposure Delayed Effects",
          "Reflex, Startle",
          "Selective Serotonin Reuptake Inhibitors",
          "Sex Factors",
          "Spermatozoa",
          "Stress, Psychological"
        ],
        "pub_types": [
          "Journal Article",
          "Randomized Controlled Trial",
          "Research Support, N.I.H., Extramural"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:23699511",
      "study_type": "rct",
      "title": "Paternal stress exposure alters sperm microRNA content and reprograms offspring HPA stress axis regulation.",
      "year": 2013
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41467-021-23353-z",
          "pmid": "34035273",
          "title": "In vivo partial reprogramming of myofibers promotes muscle regeneration by remodeling the stem cell niche."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "unknown",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s41467-021-23353-z",
        "pmid": "34035273"
      },
      "metadata": {
        "hallmark_tags": [
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Animals",
          "Cell Differentiation",
          "Cells, Cultured",
          "Cellular Reprogramming",
          "Female",
          "Gene Expression",
          "Kruppel-Like Factor 4",
          "Kruppel-Like Transcription Factors",
          "Mice, Transgenic",
          "Myofibrils",
          "Octamer Transcription Factor-3",
          "Proto-Oncogene Proteins c-myc",
          "Regeneration",
          "SOXB1 Transcription Factors",
          "Satellite Cells, Skeletal Muscle",
          "Stem Cell Niche",
          "Wnt4 Protein"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't",
          "Research Support, U.S. Gov't, Non-P.H.S."
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:34035273",
      "study_type": "observational",
      "title": "In vivo partial reprogramming of myofibers promotes muscle regeneration by remodeling the stem cell niche.",
      "year": 2021
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.celrep.2025.115298",
          "pmid": "39937646",
          "title": "Cellular rejuvenation protects neurons from inflammation-mediated cell death."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1016/j.celrep.2025.115298",
        "pmid": "39937646"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "Kruppel-Like Factor 4",
          "Mice",
          "Rejuvenation",
          "Humans",
          "Inflammation",
          "Encephalomyelitis, Autoimmune, Experimental",
          "Cell Death",
          "Retinal Ganglion Cells",
          "Mice, Inbred C57BL",
          "Neurons",
          "Dependovirus",
          "Multiple Sclerosis",
          "Female",
          "Transcriptome"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:39937646",
      "study_type": "in_vitro",
      "title": "Cellular rejuvenation protects neurons from inflammation-mediated cell death.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1089/cell.2023.0072",
          "pmid": "38381405",
          "title": "Gene Therapy-Mediated Partial Reprogramming Extends Lifespan and Reverses Age-Related Changes in Aged Mice."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1089/cell.2023.0072",
        "pmid": "38381405"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Aged",
          "Male",
          "Humans",
          "Animals",
          "Mice",
          "Longevity",
          "Aging",
          "Genetic Therapy",
          "Keratinocytes",
          "Cellular Reprogramming"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:38381405",
      "study_type": "in_vitro",
      "title": "Gene Therapy-Mediated Partial Reprogramming Extends Lifespan and Reverses Age-Related Changes in Aged Mice.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1038/s41580-019-0204-5",
          "pmid": "32020082",
          "title": "The ageing epigenome and its\u00a0rejuvenation."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1038/s41580-019-0204-5",
        "pmid": "32020082"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Aging",
          "Animals",
          "Chromatin Assembly and Disassembly",
          "DNA Methylation",
          "Epigenesis, Genetic",
          "Epigenome",
          "Epigenomics",
          "Gene Expression Regulation",
          "Histone Code",
          "Humans",
          "Longevity",
          "Rejuvenation"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:32020082",
      "study_type": "observational",
      "title": "The ageing epigenome and its\u00a0rejuvenation.",
      "year": 2020
    },
    {
      "citations": [
        {
          "doi": "10.3389/fcell.2024.1343106",
          "pmid": "38371924",
          "title": "Next-generation direct reprogramming."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "unknown",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 5,
      "ids": {
        "doi": "10.3389/fcell.2024.1343106",
        "pmid": "38371924"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "cell",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38371924",
      "study_type": "in_vitro",
      "title": "Next-generation direct reprogramming.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-023-00527-6",
          "pmid": "38102202",
          "title": "The Information Theory of Aging."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 5,
      "ids": {
        "doi": "10.1038/s43587-023-00527-6",
        "pmid": "38102202"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Epigenesis, Genetic",
          "DNA Methylation",
          "Information Theory",
          "Histones"
        ],
        "pub_types": [
          "Journal Article",
          "Review",
          "Research Support, Non-U.S. Gov't",
          "Research Support, N.I.H., Extramural"
        ]
      },
      "population_class": "cell",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38102202",
      "study_type": "in_vitro",
      "title": "The Information Theory of Aging.",
      "year": 2023
    },
    {
      "citations": [
        {
          "doi": "10.1093/humupd/dmac010",
          "pmid": "35259267",
          "title": "DNA methylation profiles after ART during human lifespan: a systematic review and meta-analysis."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1093/humupd/dmac010",
        "pmid": "35259267"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Adult",
          "Animals",
          "Child",
          "DNA",
          "DNA Methylation",
          "Female",
          "Fertilization in Vitro",
          "Genomic Imprinting",
          "Humans",
          "Infertility",
          "Longevity",
          "Pregnancy"
        ],
        "pub_types": [
          "Journal Article",
          "Meta-Analysis",
          "Systematic Review",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:35259267",
      "study_type": "meta_analysis",
      "title": "DNA methylation profiles after ART during human lifespan: a systematic review and meta-analysis.",
      "year": 2022
    },
    {
      "citations": [
        {
          "doi": "10.1158/2159-8290.CD-18-1474",
          "pmid": "31085557",
          "title": "Aging Human Hematopoietic Stem Cells Manifest Profound Epigenetic Reprogramming of Enhancers That May Predispose to Leukemia."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1158/2159-8290.CD-18-1474",
        "pmid": "31085557"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Cell Differentiation",
          "Cellular Reprogramming",
          "Cellular Senescence",
          "Cytosine",
          "DNA Methylation",
          "Disease Susceptibility",
          "Enhancer Elements, Genetic",
          "Epigenesis, Genetic",
          "Gene Expression Profiling",
          "Gene Expression Regulation, Leukemic",
          "Hematopoietic Stem Cells",
          "Histones",
          "Humans",
          "Kruppel-Like Factor 6",
          "Leukemia",
          "Promoter Regions, Genetic",
          "Transcription Factors"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, N.I.H., Extramural",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:31085557",
      "study_type": "in_vitro",
      "title": "Aging Human Hematopoietic Stem Cells Manifest Profound Epigenetic Reprogramming of Enhancers That May Predispose to Leukemia.",
      "year": 2019
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-024-00594-3",
          "pmid": "38553564",
          "title": "Restoration of neuronal progenitors by partial reprogramming in the aged neurogenic niche."
        }
      ],
      "directness_flags": [],
      "effect_direction": "unknown",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1038/s43587-024-00594-3",
        "pmid": "38553564"
      },
      "metadata": {
        "hallmark_tags": [
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Mice",
          "Animals",
          "Neurons",
          "Neurogenesis",
          "Neural Stem Cells",
          "Cell Differentiation",
          "Cellular Reprogramming"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:38553564",
      "study_type": "in_vitro",
      "title": "Restoration of neuronal progenitors by partial reprogramming in the aged neurogenic niche.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.immuni.2024.07.001",
          "pmid": "39053462",
          "title": "Innate immune training restores pro-reparative myeloid functions to promote remyelination in the aged central nervous system."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 4,
      "ids": {
        "doi": "10.1016/j.immuni.2024.07.001",
        "pmid": "39053462"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "Immunity, Innate",
          "Mice",
          "Aging",
          "Remyelination",
          "Microglia",
          "Myeloid Cells",
          "Central Nervous System",
          "Mice, Inbred C57BL",
          "Myelin Sheath",
          "Epigenesis, Genetic",
          "Demyelinating Diseases",
          "Disease Models, Animal"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "animal",
      "quality_flags": [
        "preclinical_translation_risk"
      ],
      "source": "pubmed",
      "study_key": "pmid:39053462",
      "study_type": "observational",
      "title": "Innate immune training restores pro-reparative myeloid functions to promote remyelination in the aged central nervous system.",
      "year": 2024
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
        "level_1": 34.0,
        "level_2": 28.0,
        "level_3": 16.0,
        "level_4": 8.0,
        "level_5": 3.4
      },
      "consistency_bonus": 4.0,
      "endpoint_counts": {
        "clinical_hard": 16,
        "clinical_intermediate": 4,
        "mechanistic_only": 19,
        "surrogate_biomarker": 2
      },
      "hallmark_tag_count": 8,
      "human_count": 31,
      "level_counts": {
        "1": 2,
        "2": 3,
        "3": 26,
        "4": 8,
        "5": 2
      },
      "quality_flags": {
        "no_registry_results": 10,
        "not_completed": 6,
        "observational_risk_confounding": 19,
        "preclinical_translation_risk": 10,
        "small_n_or_unknown": 5
      },
      "quality_penalty": 32.0
    },
    "final_confidence": 69.0,
    "mp": 27.0,
    "penalties": [
      {
        "count": 19,
        "delta": -6.0,
        "flag": "observational_risk_confounding",
        "kind": "quality"
      },
      {
        "count": 10,
        "delta": -4.0,
        "flag": "preclinical_translation_risk",
        "kind": "quality"
      },
      {
        "count": 5,
        "delta": -8.0,
        "flag": "small_n_or_unknown",
        "kind": "quality"
      },
      {
        "count": 6,
        "delta": -8.0,
        "flag": "not_completed",
        "kind": "quality"
      },
      {
        "count": 10,
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
          "doi": "10.1016/j.envint.2026.110058",
          "pmid": "41534275",
          "title": "Molecular basis for the early life sensitization of the brain to ultrafine carbonaceous particles: a study of the brain proteome, telomeres, and epigenetic modelling."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "surrogate_biomarker",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1016/j.envint.2026.110058",
        "pmid": "41534275"
      },
      "metadata": {
        "hallmark_tags": [
          "telomere_attrition",
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Animals",
          "Epigenesis, Genetic",
          "Particulate Matter",
          "Brain",
          "Mice",
          "Mice, Inbred C57BL",
          "Female",
          "Proteome",
          "Telomere",
          "Humans",
          "Pregnancy",
          "Air Pollutants",
          "Male",
          "Prenatal Exposure Delayed Effects",
          "Carbon"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:41534275",
      "study_type": "observational",
      "title": "Molecular basis for the early life sensitization of the brain to ultrafine carbonaceous particles: a study of the brain proteome, telomeres, and epigenetic modelling.",
      "year": 2026
    },
    {
      "citations": [
        {
          "doi": "10.1186/s40001-025-02751-9",
          "pmid": "40506775",
          "title": "Exercise orchestrates systemic metabolic and neuroimmune homeostasis via the brain-muscle-liver axis to slow down aging and neurodegeneration: a narrative review."
        }
      ],
      "directness_flags": [],
      "effect_direction": "harm",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1186/s40001-025-02751-9",
        "pmid": "40506775"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "proteostasis",
          "nutrient_sensing",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Aging",
          "Exercise",
          "Brain",
          "Neurodegenerative Diseases",
          "Homeostasis",
          "Liver",
          "Muscle, Skeletal",
          "Animals"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:40506775",
      "study_type": "observational",
      "title": "Exercise orchestrates systemic metabolic and neuroimmune homeostasis via the brain-muscle-liver axis to slow down aging and neurodegeneration: a narrative review.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1126/scitranslmed.adg1777",
          "pmid": "39259812",
          "title": "Targeted partial reprogramming of age-associated cell states improves markers of health in mouse models of aging."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1126/scitranslmed.adg1777",
        "pmid": "39259812"
      },
      "metadata": {
        "hallmark_tags": [
          "cellular_senescence",
          "stem_cell_exhaustion",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Animals",
          "Kruppel-Like Factor 4",
          "Aging",
          "Cellular Reprogramming",
          "Disease Models, Animal",
          "Cellular Senescence",
          "Mice",
          "Humans",
          "Cyclin-Dependent Kinase Inhibitor p16",
          "Biomarkers",
          "Progeria",
          "Dependovirus",
          "Promoter Regions, Genetic"
        ],
        "pub_types": [
          "Journal Article",
          "Research Support, Non-U.S. Gov't"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:39259812",
      "study_type": "in_vitro",
      "title": "Targeted partial reprogramming of age-associated cell states improves markers of health in mouse models of aging.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1080/03008207.2025.2528792",
          "pmid": "40642939",
          "title": "Biomarkers of aging as it relates osteoarthritis: we can't improve what we can't measure."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_hard",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1080/03008207.2025.2528792",
        "pmid": "40642939"
      },
      "metadata": {
        "hallmark_tags": [
          "genomic_instability",
          "epigenetic_alterations",
          "cellular_senescence"
        ],
        "mesh_terms": [
          "Humans",
          "Osteoarthritis",
          "Biomarkers",
          "Aging",
          "Animals",
          "Chondrocytes"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:40642939",
      "study_type": "observational",
      "title": "Biomarkers of aging as it relates osteoarthritis: we can't improve what we can't measure.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.1016/j.ymthe.2024.02.005",
          "pmid": "38332583",
          "title": "M2 macrophages secrete glutamate-containing extracellular vesicles to alleviate osteoporosis by reshaping osteoclast precursor fate."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1016/j.ymthe.2024.02.005",
        "pmid": "38332583"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "intercellular_communication"
        ],
        "mesh_terms": [
          "Humans",
          "Osteoclasts",
          "Glutamic Acid",
          "Macrophages",
          "Osteoporosis",
          "Extracellular Vesicles"
        ],
        "pub_types": [
          "Journal Article"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:38332583",
      "study_type": "in_vitro",
      "title": "M2 macrophages secrete glutamate-containing extracellular vesicles to alleviate osteoporosis by reshaping osteoclast precursor fate.",
      "year": 2024
    },
    {
      "citations": [
        {
          "doi": "10.1097/FJC.0000000000001752",
          "pmid": "40815481",
          "title": "Integration of Vascular Smooth Muscle Cell Phenotypic Switching and Senescence."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 1,
      "ids": {
        "doi": "10.1097/FJC.0000000000001752",
        "pmid": "40815481"
      },
      "metadata": {
        "hallmark_tags": [
          "cellular_senescence",
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Cellular Senescence",
          "Humans",
          "Muscle, Smooth, Vascular",
          "Animals",
          "Myocytes, Smooth Muscle",
          "Cell Transdifferentiation",
          "Phenotype",
          "Cardiovascular Diseases",
          "Signal Transduction",
          "Cell Plasticity",
          "Senescence-Associated Secretory Phenotype",
          "Vascular Calcification",
          "Cell Proliferation"
        ],
        "pub_types": [
          "Journal Article",
          "Systematic Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [],
      "source": "pubmed",
      "study_key": "pmid:40815481",
      "study_type": "meta_analysis",
      "title": "Integration of Vascular Smooth Muscle Cell Phenotypic Switching and Senescence.",
      "year": 2025
    },
    {
      "citations": [
        {
          "doi": "10.2741/4890",
          "pmid": "33049666",
          "title": "From genoprotection to rejuvenation."
        }
      ],
      "directness_flags": [],
      "effect_direction": "benefit",
      "endpoint_class": "clinical_intermediate",
      "evidence_level": 3,
      "ids": {
        "doi": "10.2741/4890",
        "pmid": "33049666"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations",
          "nutrient_sensing",
          "stem_cell_exhaustion"
        ],
        "mesh_terms": [
          "Aging",
          "Animals",
          "Epigenesis, Genetic",
          "Humans",
          "Longevity",
          "Rejuvenation",
          "Signal Transduction"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:33049666",
      "study_type": "in_vitro",
      "title": "From genoprotection to rejuvenation.",
      "year": 2021
    },
    {
      "citations": [
        {
          "doi": "10.1038/s43587-023-00539-2",
          "pmid": "38102454",
          "title": "Mechanisms, pathways and strategies for rejuvenation through epigenetic reprogramming."
        }
      ],
      "directness_flags": [
        "indirect_endpoint"
      ],
      "effect_direction": "benefit",
      "endpoint_class": "mechanistic_only",
      "evidence_level": 3,
      "ids": {
        "doi": "10.1038/s43587-023-00539-2",
        "pmid": "38102454"
      },
      "metadata": {
        "hallmark_tags": [
          "epigenetic_alterations"
        ],
        "mesh_terms": [
          "Humans",
          "Animals",
          "Mice",
          "Rejuvenation",
          "Aging",
          "Cellular Reprogramming",
          "Induced Pluripotent Stem Cells",
          "Epigenesis, Genetic"
        ],
        "pub_types": [
          "Journal Article",
          "Review"
        ]
      },
      "population_class": "human",
      "quality_flags": [
        "observational_risk_confounding"
      ],
      "source": "pubmed",
      "study_key": "pmid:38102454",
      "study_type": "in_vitro",
      "title": "Mechanisms, pathways and strategies for rejuvenation through epigenetic reprogramming.",
      "year": 2024
    }
  ],
  "trial_registry_rows": []
}
```
