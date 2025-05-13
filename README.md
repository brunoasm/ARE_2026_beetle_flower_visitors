# Flower Beetle Review: Data Processing and Analysis Pipeline

This repository contains the code used for data collection, processing, and analysis for our study on beetles as flower visitors and pollinators across plant families. The scripts automate a reproducible workflow from literature search to data extraction and visualization. Citation:

``` de Medeiros BAS, Peris D. 2026. The Evolution of Flower Beetles as Visitors and Pollinators. Annual Review of Entomology. in press. ```

## Overview

In this study, we investigated patterns in flower beetles across plant families using a comprehensive literature search and analysis approach. The workflow combines Web of Science searches, automated filtering with AI (Anthropic's Claude), systematic PDF processing, and data visualization to identify patterns in beetle-plant interactions worldwide.

## Workflow

The data processing pipeline consists of the following major steps:

1. **Literature search**: Using Web of Science to search for publications related to plant-pollinator interactions across 1,332 plant families
2. **Literature filtering**: Combining and filtering search results using AI to identify relevant publications
3. **PDF acquisition**: Downloading and organizing scientific papers
4. **PDF processing**: Optimizing PDFs and extracting flower visitor data using AI
5. **Data analysis**: Analyzing and visualizing geographical and phylogenetic patterns
6. **Output generation**: Creating tables and figures for publication

## Installation

### Environment Setup

You can recreate the conda environment with all necessary dependencies using:

```bash
conda env create -f conda_env.yml
conda activate ARE_review
```

### API Key

For scripts using the Anthropic API (Claude), you need to set your [Anthropic API key](https://www.anthropic.com/api):

```bash
export ANTHROPIC_API_KEY="your_api_key_here"
```

## Scripts

### 1. Literature Search

To obtain a list of plant families, we used the World Flora Online:

```
T.W.F.O. Consortium. 2024. World Flora Online Plant List June 2024. https://doi.org/10.5281/zenodo.12171908
```

From World Flora online, we downloaded `_uber.zip`, which contains all taxa, whether deprecated or not. After unzipping, we extracted all family names from the classification:

```bash
awk -F',' '$5=="family" {print $8}' classification.csv | sort > plant_families.txt
```

Next, we developed a multilingual search query incorporating terms related to pollination processes and flower visitation in the ten most common languages used in scientific publications. The query was deliberately designed to be neutral regarding pollination vectors, avoiding taxonomic or animal-specific terms that could bias the search towards particular types of pollinators. All searches had the form:

```
[plant_family] AND (pollinat* OR pollenizer* OR pollenator* OR "flower visit*" OR poliniza* OR "visita* floral*" OR besträub* OR "Blütenbesuch*" OR pollinis* OR "visite* florale*" OR poliniza* OR "visita* floral*" OR 授粉 OR shòufěn OR 授粉者 OR shòufěnzhě OR 花访 OR huāfǎng OR 授粉 OR jufun OR 授粉者 OR jufunsha OR 花の訪問 OR "hana no hōmon" OR опылен* OR opylen* OR "цветочн* посещен*" OR "tsvetochn* poseshchen*" OR impollin* OR "visita* floral*" OR 수분 OR subun OR 수분매개자 OR subunmaegaeja OR 꽃방문 OR kkotbangmun)
```

where `[plant_family]` was replaced by each one of the 1,332 plant families obtained from World Flora Online. If the search returned any results, a bibtex file with the plant family name was saved in folder [`WoS_exports/`](WoS_exports/) using the export function in Web of Science.

#### [`01_combine_bibtex.py`](scripts/01_combine_bibtex.py)

Combines multiple BibTeX files from Web of Science searches into consolidated files.

**Usage:**
```bash
python scripts/01_combine_bibtex.py
```

**Details:**
- Reads all `.bib` files in the `WoS_exports` directory
- Splits entries into two output files based on DOI presence
- Deduplicates entries
- Outputs `unfiltered_doi.bib` and `unfiltered_nodoi.bib` to the `analysis` directory

### 2. Literature Filtering

#### [`02_filter_bibtex.py`](scripts/02_filter_bibtex.py)

Uses Claude AI to analyze paper abstracts and identify studies containing primary flower visitor data.

**Usage:**
```bash
python scripts/02_filter_bibtex.py [--use-batches] [--test]
```

**Details:**
- Processes BibTeX entries from `analysis/unfiltered_doi.bib`
- Uses Anthropic Sonnet model to evaluate whether papers contain primary data on flower visitors
- Identifies papers making pollinator inferences based on plant morphology
- Saves results to `analysis/flower_visitor_classifications.json` and `analysis/classified_studies.csv`
- Options:
  - `--use-batches`: Use the Anthropic Batches API for faster processing
  - `--test`: Process only 10 records (for testing)


### 3. Literature Download

#### [`03_download_pdfs.py`](scripts/03_download_pdfs.py)

Facilitates the download of PDFs for papers identified as containing flower visitor data.

**Usage:**
```bash
python scripts/03_download_pdfs.py
```

**Details:**
- Processes entries from `analysis/classified_studies.csv` that have been marked as containing flower visitor data
- Retrieves URL information from DOIs using Crossref API
- Opens batches of links in a browser with institutional proxy access
- Allows for manual downloading and curation using Zotero
- Requires environment variables for proxy URLs:
  - `UNIVERSITY_PROXY_URL`
  - `UNIVERSITY2_PROXY_URL`

After downloading, we manually curated the library in Zotero including merging duplicates, and then exported a bibtex file and associated PDFs to the `pdfs` folder. For PDFs that did not have OCR, we processed them with Adobe Acrobat.

### 4. PDF Export Curation

#### [`04_fix_bibtex.py`](scripts/04_fix_bibtex.py)

Cleans and organizes BibTeX files and associated PDFs exported from reference managers.

**Usage:**
```bash
python scripts/04_fix_bibtex.py
```

**Details:**
- Removes duplicate `annote` fields from BibTeX entries
- Processes the `file` field to extract PDF paths
- Organizes PDFs with standardized names based on citation keys
- Creates backup copies of files
- Updates BibTeX file with corrected file paths

#### [`05_compress_pdfs.sh`](scripts/05_compress_pdfs.sh)

Compresses PDF files to reduce file size while maintaining readability.

**Usage:**
```bash
bash scripts/05_compress_pdfs.sh
```

**Details:**
- Processes PDF files in `pdfs/export_20250131/files_kept` directory
- Uses `pdftops` and `ps2pdf` for compression
- Compresses files in parallel for efficiency
- Preserves originals if compression doesn't reduce file size
- Requires: `pdftops`, `ps2pdf`, `bc`, and GNU `parallel`

After compression, we quickly checked thumbnails of all files to make sure the main text was kept, and not a supplement.

### 5. PDF Data Extraction

#### [`06_summarize_pdfs.py`](scripts/06_summarize_pdfs.py)

Uses Claude AI to extract structured data about flower visitors from PDF content.

**Usage:**
```bash
python scripts/06_summarize_pdfs.py [--output OUTPUT_FILE] [--test]
```

**Details:**
- Reads PDFs from paths specified in the BibTeX file
- Uses Anthropic's Claude 3.5 Sonnet model to extract flower visitor records
- Extracts detailed information including:
  - Location (country, state/province, locality)
  - Plant species
  - Observation methods
  - Time of observation
  - List of flower visitors
  - Beetle visitor and pollinator presence
  - Assessment of methodology bias
- Processes PDFs in parallel batches for efficiency
- Saves structured data to `analysis/flower_visitor_records.json`
- Options:
  - `--output`: Specify output JSON file path
  - `--test`: Process a limited number of PDFs for testing

### 6. Output Generation

#### [`07_format_supp_table_2.py`](scripts/07_format_supp_table_2.py)

Creates formatted supplementary table for the paper.

**Usage:**
```bash
python scripts/07_format_supp_table_2.py
```

**Details:**
- Reads CSV data from `tables/plant_family_table.csv`
- Parses BibTeX references
- Formats citations using Annual Reviews style
- Creates an RTF document with the formatted table and references
- Outputs to `tables/draft_table_2.rtf`

## Data Analysis and Visualization

Data analysis and visualization are primarily handled in the R notebook:

- [`plots_and_stats.Rmd`](plots_and_stats.Rmd): R notebook that includes code for creating figures and statistical analyses
  - Processes results from `analysis/flower_visitor_records.json`
  - Maps locations to standardized geographic data
  - Reconciles plant taxonomic names with World Flora Online
  - Generates maps, phylogenetic trees, and statistical summaries
  - Creates publication-ready figures

## Data Files

### Input Data
- [`plant_families.txt`](plant_families.txt): List of plant families from World Flora Online
- [`WoS_exports/`](WoS_exports/): Directory containing BibTeX files from Web of Science searches

### Intermediate Data
- [`analysis/classified_studies.csv`](analysis/classified_studies.csv): CSV file with classified papers
- [`analysis/flower_visitor_classifications.json`](analysis/flower_visitor_classifications.json): JSON file with detailed classifications
- [`analysis/flower_visitor_records.json`](analysis/flower_visitor_records.json): JSON file with extracted flower visitor data

### Output Data
- [`figures/`](figures/): Directory containing generated figures
- [`tables/`](tables/): Directory containing generated tables

## License

See the [`LICENSE`](LICENSE) file for details.