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

We then combined all bibtex files in `WoS_exports` to the file `combined_unfiltered.bib` in the folder `analysis/`.



3. Literature download
Since a lot of the papers are paywalled, we will take a semi-automated approach. We will have a script opening browser windows, and then use Zotero extension to save the pdfs into a collection.

4. 
