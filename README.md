# Summarizing trends about beetle pollinators

In this repository, we want to understand what are the general trends about pollinator research. In particular, we want to evaluate whether there are geographical trends in research finding beetle pollinators. This takes the follwing steps:

1. Literature search
We will use crossref API to gather as many papers as we can. For this, we will take a plant-centric approach: we will get a list of all plant families (valid and invalid) and do queries of the form `[plant family] + "pollinator"`. We will retrieve dois and basic data for all papers found, as well as for all papers cited in them.

Plant families have been obtained from World Flora online:
```
T.W.F.O. Consortium. 2024. World Flora Online Plant List June 2024. https://doi.org/10.5281/zenodo.12171908

```
After downloading the zipped file, we extracted plant families with this python oneliner. In this repository, we will keep only the family names.

```
open('plant_families.txt', 'w').write('\n'.join(pd.concat(chunk[chunk['taxonRank'] == 'family']['scientificName'] for chunk in pd.read_csv(io.TextIOWrapper(zipfile.ZipFile('WFO_Backbone.zip').open([f for f in zipfile.ZipFile('WFO_Backbone.zip').namelist() if f.endswith('.csv')][0]), encoding='latin-1'), sep='\t', chunksize=10000)).unique()))
```

3. Literature download
Since a lot of the papers are paywalled, we will take a semi-automated approach. We will have a script opening browser windows, and then use Zotero extension to save the pdfs into a collection.

4. 
