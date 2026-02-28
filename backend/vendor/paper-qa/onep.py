import csv

# Les données à écrire dans le CSV, basées sur votre document
# Chaque tuple représente une ligne (Clé, Valeur)
data = [
    ('FICHE SYNTHÉSIQUE', ''),
    ('Nom de la startup', 'DeltaWave'),
    ('Contact (site web, linkedin, mail, téléphone, localisation)', 
     "https://www.deltawave.fr/ \n"
     "https://www.linkedin.com/company/deltawave/?viewAsMember=true\n"
     "ahmet@deltawave.fr\n"
     "+33 6 33 71 88 71\n"
     "145 RUE DE NOISY-LE-SEC 93260 LES LILAS"),
    ('Problème', 
     "Malgré les progrès de l'Intelligence Artificielle (IA), la découverte de médicaments reste bloquée\n"
     "Le développement d'un nouveau médicament coûte encore 2,6 milliards de dollars et prend 10 à 15 ans, avec un taux d'échec de 95 %. Pourquoi ? Quatre défis majeurs empêchent l'IA et les outils computationnels de tenir leurs promesses :\n\n"
     "1) 60 % des entreprises de biotechnologie n'utilisent pas l'IA, car c'est trop complexe\n\n"
     "Bien que l'IA ait un potentiel immense, elle est trop complexe pour être largement adoptée. La sélection des modèles, leur réglage fin (fine-tuning), leur déploiement et leur maintenance continue nécessitent une expertise que la plupart des entreprises n'ont pas. \n"
     "Cela empêche la majorité des entreprises de bénéficier du potentiel de l'IA.\n\n"
     "2) La dépendance des chimistes envers les experts en IA entraîne retards et inefficacités\n\n"
     "Même lorsque les entreprises investissent dans une infrastructure d'IA, les chimistes ne peuvent pas l'exploiter pleinement.\n"
     "Pour chaque expérience – prédire les propriétés des composés, optimiser des molécules ou effectuer des simulations de docking – ils dépendent des experts en machine learning.\n"
     "Cette situation ralentit les progrès, crée de l'inefficacité et limite le potentiel des chimistes.\n"
     "3) Les outils fragmentés créent des silos de données et des pertes d'insights\n\n"
     "La découverte de médicaments nécessite d'intégrer bien plus que l'IA. Les chimistes \n"
     "s'appuient sur plus de 100 sources fragmentées, incluant des modèles d'IA, des bases de données moléculaires (ex: PubChem, ChEMBL), des dépôts de littérature (ex: PubMed, bioRxiv), des méthodes de mécanique quantique et divers outils SaaS.\n"
     "Cette structure fragmentée crée des silos de données, complique le flux et l'interprétation des données, et entraîne la perte d'insights critiques qui pourraient mener à des percées.\n"
     "4) Tous ces problèmes créent des inefficacités cumulées dans les processus longs et interdisciplinaires de découverte de médicaments\n\n"
     "Ces défis accentuent les inefficacités dans les processus collaboratifs DMTA (Conception-Synthèse-Test-Analyse), qui durent 5 à 7 ans et nécessitent en moyenne 17 itérations.\n"
     "Les ruptures de communication, les silos de données et le turnover élevé du personnel entraînent la perte d'informations critiques.\n"
     "Les précieux enseignements, qu'ils proviennent de succès ou d'échecs, sont rarement enregistrés ou réutilisés systématiquement, ce qui limite la capacité à accélérer les futures itérations.\n"
     ""),
    ('Solution', 
     "Solution\n\n"
     "DeltaWave : Une nouvelle façon de travailler dans la découverte de médicaments\n"
     "DeltaWave rend l'IA plus accessible que jamais et unifie toutes les ressources et les flux de travail de l'entreprise au sein d'une plateforme intuitive et collaborative.\n"
     "Nous recherchons, comparons, déployons, hébergeons et maintenons constamment à jour les meilleurs modèles d'IA open source pour la découverte de médicaments, tels qu'AlphaFold, DiffDock et les modèles ADMET, les rendant ainsi utilisables sans expertise requise.\n"
     "Mais nous ne nous arrêtons pas là. Nous intégrons la littérature, les modèles de mécanique quantique, les API SaaS d'autres entreprises, les modèles internes, les graphes de connaissances et les bases de données de fournisseurs comme Enamine, créant ainsi un écosystème unifié où tous les outils et ressources sont interconnectés.\n"
     "DeltaWave est plus que la somme de ses parties. Notre outil d'IA évolutif apprend avec le temps, assistant les chimistes dans leurs tâches quotidiennes – de l'utilisation des modèles d'IA à l'interprétation des résultats, l'analyse des données et l'automatisation des processus de bout en bout.\n"
     "DeltaWave orchestre l'ensemble de ces outils pour le chimiste, réduit la perte de données à zéro et démultiplie leur capacité à innover.\n"
     "Pensez-y : Imaginez non pas 1, mais 1 000 étudiants en master travaillant chaque jour aux côtés des chimistes ou des biologistes structurels.\n"
     "DeltaWave transforme les flux de travail de la découverte de médicaments en donnant aux scientifiques le pouvoir d'exploiter tout le potentiel de l'IA, réduisant les coûts, raccourcissant les délais et accélérant les percées."),
    ('Marché Cible', 
     "Notre marché principal est constitué des grandes entreprises pharmaceutiques (1 000+ employés), en développant des PoC internes via des programmes d'intrapreneuriat, comme nous le faisons avec Servier.\n"
     "Tout en ciblant les grandes entreprises pharmaceutiques, nous nous concentrons également sur les petites et moyennes entreprises de biotechnologie, qui ont un besoin plus urgent d'une plateforme unifiée.\n"
     "En nous concentrant initialement sur la découverte de médicaments, nous prévoyons de nous étendre d'ici 3 à 5 ans à l'agrochimie, à la conception de matériaux et à d'autres secteurs axés sur les molécules.\n"
     "\nEn commençant par l'Europe, nous visons à entrer aux États-Unis la deuxième année, qui représentent 50 % du marché pharmaceutique mondial."),
    ('', ''),
    ('Clients Actuels', 
     "Nous n'avons pas encore de clients.\n"
     "Nous sommes en discussions avancées pour lancer un projet pilote avec Bayer et travaillons à lancer un PoC interne au sein de l'incubateur d'une entreprise pharmaceutique française."),
    ('PoC (Rémunéré)', 'Aucun PoC démarré pour le moment'),
    ('PoC (non rémunéré)', 
     "Aux premiers stades de DeltaWave, nous avons développé un outil statistique analysant les données d'essais cliniques de phase 1 pour une société de biotechnologie spécialisée dans la maladie d'Alzheimer.\n"
     "L'agent a traité les données, effectué des analyses en les comparant aux résultats du CRO et a fourni des insights.\n"
     "L'entreprise a été impressionnée par le produit et a proposé un projet payant pour la phase 2. Cependant, nous n'avons pas poursuivi, car nous nous concentrons sur la conception de molécules.\n"
     "Ce travail a validé la flexibilité de notre agent dans les tâches scientifiques."),
    ('Avantage Concurrentiel', 
     "Basé sur deux tendances transformatrices :\n"
     "DeltaWave tire parti de la croissance rapide des modèles d'IA open source et des LLM (Grands Modèles de Langage) qui révolutionnent la découverte de médicaments.\n"
     "Ces domaines évoluent rapidement, et DeltaWave fait le lien entre ces deux mondes.\n"
     "Contrairement aux concurrents qui développent des modèles propriétaires, DeltaWave évolue au rythme de ces technologies, assurant une croissance continue et exponentielle des capacités de la plateforme.\n"
     "Transparence et Explicabilité :\n\n"
     "L'utilisation par DeltaWave de modèles open source offre une transparence totale – les utilisateurs connaissent l'architecture, les poids et la logique de chaque décision de l'IA.\n"
     "L'agent permet une meilleure compréhension des résultats des modèles, aidant les chimistes à prendre des décisions plus éclairées et réduisant l'effet de \"\"boîte noire\"\".\n"
     "Cela renforce la confiance, améliore les compétences des utilisateurs et crée un engagement à long terme.\n"
     "Agent Apprenant : Dépendance à Long Terme :\n\n"
     "L'agent de DeltaWave apprend les flux de travail spécifiques à l'entreprise, préserve le savoir institutionnel et s'améliore à chaque interaction.\n"
     "Cela rend la plateforme indispensable : même lorsque les employés partent, l'expertise reste au sein de DeltaWave, assurant la continuité des opérations de l'entreprise.\n"
     "À mesure que l'agent évolue, il réduit les frictions entre les parties prenantes et améliore la collaboration, créant ainsi un avantage concurrentiel unique et durable.\n"
     "Scalabilité et Effets de Réseau :\n\n"
     "DeltaWave crée un effet de réseau : chaque outil ou modèle (open source ou propriétaire) intégré à la plateforme augmente sa valeur, attirant davantage d'entreprises pharmaceutiques et de développeurs.\n"
     "Comme eBay ou Hugging Face, ce cycle renforce la domination de DeltaWave avec le temps.\n"
     "Pour les développeurs de modèles (ex: AlphaFold, BiOptimus), héberger leurs outils sur DeltaWave facilite leur accès aux entreprises pharmaceutiques.\n"
     "Plus les entreprises pharmaceutiques rejoignent la plateforme, plus elles encouragent l'intégration de nouveaux outils.\n"
     "Positionnement à l'épreuve du futur :\n\n"
     "Les concurrents qui misent sur le développement de modèles propriétaires risquent d'être dépassés par les percées de l'open source.\n"
     "La stratégie de DeltaWave, qui consiste à intégrer et à amplifier ces innovations, la maintient à la pointe de l'innovation dans la découverte de médicaments, sans le fardeau du développement propriétaire."),
    ('Concurrents', 
     "Iktos, Chempass : Plateformes SaaS de génération de molécules, spécialisées dans la conception de composés assistée par IA.\n"
     "BioNemo, Tamarind, 310.ai : Plateformes low-code/no-code regroupant des modèles open source pour la découverte de médicaments.\n"
     "Bioptimus : Une plateforme fondamentale axée sur le développement de grands modèles de langage pour la découverte de médicaments.\n"
     "Owkin, Aqemia : Plateformes de services d'IA combinant logiciels et conseil pour la recherche biopharmaceutique."),
    ('', ''),
    ('Fondateurs', 
     "Co-CEO/Chief AI Officer : Ahmet Çelebi\n"
     "Après une formation de mathématicien, Ahmet Çelebi a débuté sa carrière en tant que chercheur quantitatif dans le secteur bancaire, mais a refusé une offre de doctorat en raison de sa passion pour l'entrepreneuriat.\n"
     "Après avoir fondé une startup crypto à succès, il s'est tourné vers l'IA, se spécialisant dans l'IA théorique et les systèmes basés sur des agents.\n"
     "Pendant trois ans, il a travaillé comme chercheur et ingénieur dans diverses startups d'IA en France, acquérant une expérience directe dans le développement de solutions d'IA scalables.\n"
     "Animé par la passion de démocratiser l'IA dans des domaines à fort impact comme la santé, Ahmet a cofondé DeltaWave en combinant son expertise en automatisation et en accessibilité de l'IA.\n"
     "Co-CEO/CTO\n"
     "Avec plus de six ans d'expérience en leadership dans la robotique, les véhicules électriques autonomes et la cybersécurité, notre CTO apporte une expertise approfondie dans la création de systèmes d'IA scalables dans divers secteurs.\n"
     "Ayant fondé deux startups et occupé le poste de CTO dans des entreprises à forte croissance, notre CTO est expérimenté dans la conduite de l'innovation stratégique et la livraison de solutions impactantes sur le marché.\n"
     "Son expérience dans la mise à l'échelle de modèles de machine learning et l'optimisation de l'infrastructure permet à DeltaWave de répondre aux exigences de calcul élevées de la découverte de médicaments moderne.\n"
     "Fondateur Chercheur BIO-AI : Atabey Ünlü\n"
     "Atabey Ünlü est un chercheur en biotechnologie et en IA, titulaire d'un master de haut niveau en bioinformatique et d'un doctorat axé sur les modèles génératifs pour la conception moléculaire et protéique.\n"
     "Ses travaux ont été publiés dans des revues et conférences prestigieuses, faisant de lui un leader d'opinion dans les sciences de la vie computationnelles.\n"
     "Chez DeltaWave, Atabey pilote l'innovation en unifiant les données et les flux de travail fragmentés grâce à des pipelines de pointe basés sur l'IA.\n"
     "Son expertise des modèles génératifs et des collaborations industrielles garantit que DeltaWave reste à la pointe de la découverte de médicaments assistée par IA.\n"
     ""),
    ('Experts IA', "L'expertise en IA de DeltaWave est portée par ses deux cofondateurs, Ahmet Çelebi et notre CTO, ainsi que par notre chercheur fondateur BIO-AI, Atabey Ünlü."),
    ('Nombre d\'employés', "7 employés : 2 fondateurs, 1 ingénieur fondateur, 1 développeur frontend freelance et 3 stagiaires.\n"),
    ('', ''),
    ('Stade (Pré-amorçage, amorçage, Série A, etc.)', 'pre-seed (pré-amorçage)'),
    ('Modèle Économique', 
     "Abonnements SaaS : Offre un accès complet à la plateforme, un support technique et une intégration des flux de travail aux grandes entreprises pharmaceutiques et aux sociétés de biotechnologie.\n"
     "Modèle basé sur l'utilisation : Crée de la valeur via l'infrastructure de la plateforme (utilisation du GPU, entraînement/prédiction des modèles, hébergement).\n"
     "Partenariats d'hébergement de modèles : Collabore avec des laboratoires open source, des fournisseurs de modèles d'IA (ex: DeepMind) et d'autres sociétés SaaS pour héberger leurs modèles sur la plateforme."),
    ('Modèle de Revenus', 
     "DeltaWave génère ses revenus de la manière suivante :\n\n"
     "Frais d'abonnement :\n"
     "500 000 € par an : Pour les grandes organisations (+100 000 employés).\n"
     "Contenu : Accès complet à la plateforme, support technique, intégration des flux de travail.\n"
     "Frais basés sur l'utilisation :\n"
     "500 000 € à 10 millions € par an : Basés sur les coûts d'infrastructure tels que l'utilisation du GPU, l'entraînement/prédiction des modèles, l'hébergement.\n"
     "Partage des revenus :\n"
     "DeltaWave perçoit une marge de 10 à 30 % sur les revenus basés sur l'utilisation grâce à des partenariats avec les fournisseurs de modèles."),
    ('Technologie d\'IA utilisée', 
     "DeltaWave combine des technologies d'IA avancées avec un cadre unique basé sur des agents :\n\n"
     "Modèles Open Source : Nous curatons, comparons et déployons plus de 10 modèles de pointe, dont Boltz (repliement de protéines), DiffDock-L (docking) et des outils de prédiction ADMET.\n"
     "Nos modèles sont hébergés sur une infrastructure optimisée avec Triton.\n"
     "LLM pour la chimie : GPT, Claude et Mistral sont intégrés et affinés (fine-tuned) pour les tâches de découverte de médicaments.\n"
     "Ces modèles comprennent les entrées des chimistes, sélectionnent les outils appropriés, interprètent les résultats et expliquent les limitations des modèles, améliorant ainsi la transparence.\n"
     "Cadre Basé sur des Agents : L'agent IA de DeltaWave combine les modèles avec les bases de données de la littérature, les bibliothèques moléculaires et les données des flux de travail précédents.\n"
     "L'agent évolue grâce aux interactions avec les chimistes, automatise les flux de travail répétitifs et mémorise les stratégies réussies pour optimiser les requêtes futures.\n"
     "Système RAG : Notre système avancé de Génération Augmentée par Récupération (RAG) permet aux agents d'accéder à des informations à jour et pertinentes provenant des bases de données moléculaires et de la littérature, améliorant la précision et réduisant les hallucinations."),
    ('', ''),
    ('Articles Académiques', 
     "- Articles publiés par notre cofondateur Atabey Ünlü :\n\n"
     "Target Specific De Novo Design of Drug Candidate Molecules with Graph Transformer-based Generative Adversarial Networks : Une approche révolutionnaire sur les modèles génératifs pour la conception moléculaire.\n"
     "SELFormer: Molecular Representation Learning via SELFIES Language Models : Un travail innovant sur l'apprentissage de la représentation moléculaire pour les tâches de prédiction moléculaire.\n"
     "- Interne (en cours, non publié)\n"
     "\"\"Simulation d'un Chimiste Médicinal Virtuel\"\" (Article interne) :\n\n"
     "Nous travaillons sur un système multi-agents qui accomplit de manière autonome un processus virtuel de découverte de médicaments pour démontrer les capacités de la plateforme basée sur des agents de DeltaWave.\n"
     "Dans ce système, les agents (ex: Chimiste Médicinal, Chef de Produit, Expert IA) entreprennent la tâche de créer une bibliothèque d'inhibiteurs pour la protéine AKT1.\n"
     "Ce travail est toujours en cours et est conçu pour démontrer le potentiel d'automatisation et de scalabilité de la plateforme."),
    ('N° de brevet', 'En cours'),
    ('N° de marque', 'En cours'),
    ('', ''),
    ('Récompenses', 
     "Finaliste du Hackathon Mistral :\n\n"
     "Nous avons optimisé le modèle Mistral-7B par affinage (fine-tuning) pour offrir une alternative rentable à GPT-4 sur la plateforme basée sur des agents de ChemCrow, obtenant des performances similaires avec un modèle 1 000 fois plus petit.\n"
     "Consultez notre article Medium pour plus de détails. https://medium.com/@ahmet_celebi/create-react-fine-tuning-dataset-for-mistral-7b-instruct-v0-3-d6556bab7c56\n"
     "Concours de Pitch VivaTech :\n\n"
     "Seulement trois mois après la création de DeltaWave, nous avons été sélectionnés pour pitcher devant 300 personnalités influentes à VivaTech, la plus grande conférence technologique d'Europe.\n"
     "Nous avons obtenu la 3ème place au concours.\n"
     "Startup GenAI la plus prometteuse de France :\n\n"
     "Reconnus par un VC français réputé comme l'une des startups d'IA générative les plus prometteuses de France https://www.linkedin.com/feed/update/urn:li:activity:7239166207365386241/"),
    ('Programmes d\'accélération', 
     "Antler, Wilco : Ont fourni financement, stratégie de gouvernance et accès au réseau.\n"
     "Biolabs, Spartners : En tant qu'incubateurs d'entreprises françaises leaders de l'industrie pharmaceutique, ils ont offert une expertise et des connexions en biotechnologie.\n"
     "CNRS (Centre National de la Recherche Scientifique) : A fourni une collaboration de recherche et un soutien en infrastructure.\n"
     "NVIDIA Inception, AWS, Google Cloud, Scaleway : Ont fourni les ressources cloud et de calcul nécessaires au développement.\n"
     "GENCI (Grand Équipement National de Calcul Intensif) : A fourni l'accès à une infrastructure de calcul haute performance."),
    ('Investissements Reçus (Stade d\'investissement, montant et provenance)', 
     "Investissements Reçus\n"
     "150 000 € d'Antler (via pré-amorçage BSA AIR).\n"
     "90 000 € de BPI France (Bourse French Tech Deep Tech).\n"
     "Label Deep Tech : Une distinction accordée aux technologies innovantes impliquant des défis scientifiques et techniques majeurs.\n"
     "60 000 € de Wilco en cours d'obtention dans le cadre d'un Prêt d'honneur."),
    ('', ''),
    ('MRR (USD)', 'Non applicable'),
    ('ARR (USD)', 'Non applicable'),
    ('', ''),
    ('', ''),
    ('', ''),
    ('', 'l')
]

# Nom du fichier CSV à créer
filename = 'deltawave_info.csv'

# Écriture dans le fichier CSV
try:
    with open(filename, mode='w', newline='', encoding='utf-8-sig') as file:
        writer = csv.writer(file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        
        for row in data:
            writer.writerow(row)
            
    print(f"Le fichier '{filename}' a été créé avec succès.")

except IOError as e:
    print(f"Erreur lors de l'écriture du fichier : {e}")
