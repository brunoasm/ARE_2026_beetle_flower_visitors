# Summarizing trends about beetle pollinators

In this study, we want to understand what are the general trends about pollinator research. In particular, we want to evaluate whether there are geographical trends in research finding beetle pollinators. This takes the follwing steps:

1. Literature search
 
We will use Web of Science to obtain our references by searching terms related to pollination together with plant families. 

To obtain a list of plant families, we used the World Flora Online:

```
T.W.F.O. Consortium. 2024. World Flora Online Plant List June 2024. https://doi.org/10.5281/zenodo.12171908

```

From World Flora online, we downloaded `_uber.zip`, which contains all taxa, whether deprecated or not. After unzipping, we extract all family names from the classification with an awk command searching for taxon rank of family (i. e. the fifth column)
```
awk -F',' '$5=="family" {print $8}' classification.csv | sort > plant_families.txt
```


Next, to ensure a comprehensive literature search, we developed a multilingual search query incorporating terms related to pollination processes and flower visitation in the ten most common languages used in scientific publications (English, Chinese, German, Spanish, French, Portuguese, Japanese, Russian, Italian, and Korean). We used claude.ai to help design this search query. For each language, we included terms for pollination and flower visitation, using wildcards where appropriate to capture term variations. FFor languages using non-Latin scripts, we included complete terms without wildcards, as well as their transliterations, to maximize retrieval while adhering to database search constraints. The query was deliberately designed to be neutral regarding pollination vectors, avoiding taxonomic or animal-specific terms that could bias the search towards particular types of pollinators. All searches had the form:

```[plant_family] AND (pollinat* OR pollenizer* OR pollenator* OR "flower visit*" OR poliniza* OR "visita* floral*" OR besträub* OR "Blütenbesuch*" OR pollinis* OR "visite* florale*" OR poliniza* OR "visita* floral*" OR 授粉 OR shòufěn OR 授粉者 OR shòufěnzhě OR 花访 OR huāfǎng OR 授粉 OR jufun OR 授粉者 OR jufunsha OR 花の訪問 OR "hana no hōmon" OR опылен* OR opylen* OR "цветочн* посещен*" OR "tsvetochn* poseshchen*" OR impollin* OR "visita* floral*" OR 수분 OR subun OR 수분매개자 OR subunmaegaeja OR 꽃방문 OR kkotbangmun)```

where `[plant_family]` was replaced by each one of the 1,332 plant families obtained from World Flora Online. If the search returned any results, a bibtex file with the plant family name was saved in folder `WoS_exports` by using the export function in Web of Science.

2. Literature filtering

We then combined and deduplicates all bibtex files in `WoS_exports` to the files `unfiltered_doi.bib` and `unfiltered_nodoi.bib` in the folder `analysis/`. There were 14,443 records with a doi and 1,428 without a doi. Given that it is much easier to obtain records with a doi and they are the vast majority, we will keep those for now. This was done with script `01_combine_bibtex.py`.

Next, we used Anthropic Sonnet model to evaluate whether each record is likely to contain primary information about flower visitors. The prompt used had this form:

 * System prompt:
```
You are a meticulous pollination biologist with extensive experience in reviewing scientific literature on plant-pollinator interactions. Your task is to analyze a scientific paper's title and abstract to predict its content regarding pollination and flower visitation to produce structured data for a meta-analysis.
```

  * Message:
```
Here is the title and abstract of the paper you need to analyze:

<title>
{title}
</title>

<abstract>
{abstract}
</abstract>

Your goal is to determine two key aspects of the paper:

1. Does the paper report empirical observations of animals visiting angiosperm flowers or gymnosperm reproductive structures? (has_visitor_data)
2. Does the paper make inferences about pollination agents based solely on plant morphology/physiology, without direct observation? (infers_from_plant)

Important considerations:
- Be cautious not to confuse seed predators or other non-pollinating visitors with actual pollinators.
- Consider both angiosperms (flowering plants) and gymnosperms (non-flowering plants with analogous reproductive structures).
- Distinguish between morphological descriptions related to pollination agent estimation and those unrelated to pollination.

Based on your analysis, provide your determination in the form of a JSON object with two boolean values:
1. "has_visitor_data": true if the study likely contains primary observations or experiments about pollinators and other flower visitors, false otherwise.
2. "infers_from_plant": true if the study likely infers pollinating agents based on plant morphology/physiology alone, without direct observation, false otherwise.

Wrap your JSON response in <output> tags. For example:
<output>
{{"has_visitor_data": false, "infers_from_plant": true}}
</output>

Ensure that your determination is based solely on the information provided in the title and abstract, making your best inference about the full study's content."""
```

`{title}` and `{abstract}` were replaced with the title and abstract of each record.

Results were saved as both a json file (`analysis/flower_visitor_classifications.json`) and a csv table (`analysis/classified_studies.csv`). There were 4,851 abstracts considered promising for flower visitor information.


3. Literature download
To download paywalled papers fast, we used a script to retrieve the link from each doi using the crossref API, adding an institutional proxy to the link, and opening them in a browser in batches. We then manually download the papers with the help of Zotero connection, and curate the library at Zotero including merging duplicates. We then export a bibtex file and associated pdfs to folder `pdfs`. If some pdf did not have OCR, we did it with Adobe Acrobat.

4. PDF export curation
First, we run a script to remove duplicated pdf files (which can arise from record merges in Zotero, for example). Next, we use postscript to compress these files by reducing image quality. Finally, we quickly checked thumbnails of all files to make sure the main text was kept, and not a supplement.

5. PDF data extraction

6. Analyses
