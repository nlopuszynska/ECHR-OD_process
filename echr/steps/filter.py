#!/usr/bin/python
import argparse
from collections import Counter
import json
import os
from os import listdir, path
import copy
import re

from echr.utils.folders import make_build_folder
from echr.utils.logger import getlogger
from echr.utils.cli import TAB
from rich.markdown import Markdown
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    BarColumn,
    TimeRemainingColumn,
)

log = getlogger()


def format_parties(parties):
    """
        Return the list of parties from the case title.

        :param parties: string containing the parties name
        :type parties: str
        :return: list of names
        :rtype: [str]
    """
    if parties.startswith('CASE OF '):
        parties = parties[len('CASE OF '):]
    if parties[-1] == ')':
        parties = parties.split('(')[0]
    parties = parties.split(' v. ')
    parties = [p.strip() for p in parties]
    return parties


def split_and_format_article(article):
    """
        Return the list of articles from a string

        :param article: str
        :type article: str
        :return: list of articles
        :rtype: [str]
    """
    def remove_incorrect_prefixes(art):
        if re.match(re.compile("^\W"), art):
            return art[1:]
        return art

    parts = article.split('+')
    articles = [remove_incorrect_prefixes(parts[-1])]
    for k, e in enumerate(parts[:-1]):
        if not parts[k + 1].startswith(e):
            articles.append(remove_incorrect_prefixes(e))
    return articles


def find_base_articles(articles):
    """
        Return the base articles from a list of articles

        :param articles: list of articles
        :type articles: [str]
        :return: bases
        :rtype: [str]
    """
    base_articles = []
    for a in articles:
        a = a.split('+')[0]
        if 'p' not in a.lower():
            base_articles.append(a.split('-', 1)[0])
        else:
            base_articles.append('-'.join(a.split('-')[0:2]))
    return base_articles


def merge_conclusion_elements(elements):
    """
        Merge similar conclusion elements in a single one, more descriptive

        :param elements: conclusion elements
        :type elements: [dict]
        :return: conclusion elements
        :rtype: [dict]
    """
    final_elements = {}
    for e in elements:
        if 'article' in e and 'base_article' in e:
            key = '{}_{}_{}'.format(e['article'], e['base_article'], e['element'])
        else:
            key = e['element']
        if key not in final_elements:
            final_elements[key] = e
        final_elements[key].update(e)
    return list(final_elements.values())


def get_element_type(l):
    t = 'other'
    if l.startswith('violation'):
        t = 'violation'
    elif l.startswith('no-violation') or l.startswith('no violation'):
        t = 'no-violation'
    return t


def format_conclusion_elements(i, e, final_ccl):
    to_append = []
    l = e['element'].lower().strip()

    # Determine type
    t = get_element_type(l)
    final_ccl[i]['type'] = t
    if t == 'other':
        to_append.append(final_ccl[i])

    # Determine articles
    articles = []
    if 'protocol' in e['element'].lower():
        prot = e['element'].lower().split('protocol no.')
        f1 = prot[0].split()[-2]
        f2 = prot[1].split()[0]
        final_ccl[i]['article'] = f'p{f2}-{f1}'
        articles = split_and_format_article(final_ccl[i]['article'])

    if 'article' not in final_ccl[i] and t != 'other':
        art = None
        find_and_replace = [
            (' and art. ', ''),
            (' and of ', '+'),
            (' and ', '+')
        ]
        for p in find_and_replace:
            if p[0] in l:
                l = l.replace(p[0], p[1])

        b = l.split()
        for j, a in enumerate(b):
            if a.startswith('art'):
                if a.lower().startswith('art.') and not a.lower().startswith('art. ') and len(a) > 4:
                    art = a.lower()[4:]
                else:
                    art = b[j + 1]
                break
        if art is not None:
            articles = split_and_format_article(art)
            art = art.split('+')
            if '+' in art[0]:
                sart = art[0].split('+')
                t = [sart[-1]]
                for k, e in enumerate(sart[:-1]):
                    if not sart[k + 1].startswith(e):
                        t.append(e)

    base_articles = find_base_articles(articles)
    for k, art in enumerate(articles):
        item = copy.copy(final_ccl[i])
        item['article'] = art
        item['base_article'] = base_articles[k]
        to_append.append(item)
    return to_append


def format_conclusion(ccl):
    """
        Format a conclusion string into a list of elements:

        :Example:

        ```json
            {
                "article": "3",
                "details": [
                    "Article 3 - Degrading treatment",
                    "Inhuman treatment"
                ],
                "element": "Violation of Article 3 - Prohibition of torture",
                "mentions": [
                    "Substantive aspect"
                ],
                "type": "violation"
            },
            {
                "article": "13",
                "details": [
                    "Article 13 - Effective remedy"
                ],
                "element": "Violation of Article 13 - Right to an effective remedy",
                "type": "violation"
            }
        ```
        :param ccl: conclusion string
        :type ccl: str
        :return: list of formatted conclusion element
        :rtype: [dict]
    """
    final_ccl = []
    chunks = [c for c in ccl.split(')') if len(c)]
    art = []
    for c in chunks:
        if '(' not in c:
            art.extend(c.split(';'))
        else:
            art.append(c)
    art = [a for a in art if len(a) > 0]
    for c in art:
        a = c.split('(')
        b = a[1].split(';') if len(a) > 1 else None
        articles = [d.strip() for d in a[0].split(';')]
        articles = [d for d in articles if len(d) > 0]
        if not len(articles):
            if b:
                if 'mentions' in final_ccl[-1]:
                    final_ccl[-1]['mentions'].extend(b)
                else:
                    final_ccl[-1]['mentions'] = b
            continue
        article = articles[-1] if not articles[-1].startswith(';') else articles[-1][1:]
        conclusion = {'element': article}
        if b:
            conclusion['details'] = b
        if len(article.strip()) == 0:
            if b is not None:
                final_ccl[-1]['mentions'] = b
        else:
            final_ccl.append(conclusion)
    if len(articles) > 1:
        for a in articles[:-1]:
            if len(a) > 0:
                final_ccl.append({'element': a})

    to_append = []
    for i, e in enumerate(final_ccl):
        to_append.extend(format_conclusion_elements(i, e, final_ccl))

    final_ccl = merge_conclusion_elements(to_append)
    return final_ccl


def format_article(article):
    """
        Format the list of articles.

        :param article: string containing the list of articles
        :type article: str
        :return: list of articles
        :rtype: [str]
    """
    articles = article.lower().split(';')
    return list(set(find_base_articles(
        [item for sublist in list(map(split_and_format_article, articles)) for item in sublist])))


def format_subarticle(article):
    """
        Format the list of subarticles.

        :param article: string containing the list of articles
        :type article: str
        :return: list of subarticles
        :rtype: [str]
    """
    articles = article.split(';')
    articles = [a for sublist in articles for a in sublist.split('+')]
    res = list(set(articles))
    return res


def format_cases(console, cases):
    """
        Format the cases from raw information

        :param cases: list of cases raw information
        :type cases: [dict]
        :return: list of formatted cases
        :rtype: [dict]
    """
    COUNTRIES = {}
    with open(os.path.join('data', 'countries.json')) as f:
        data = json.load(f)
        for c in data:
            COUNTRIES[c['alpha-3']] = {
                'alpha2': c['alpha-2'].lower(),
                'name': c['name']
            }

    ORIGINATING_BODY = {}
    with open(os.path.join('data', 'originatingbody.json')) as f:
        ORIGINATING_BODY = json.load(f)

    with Progress(
            TAB + "> Format cases [IN PROGRESS]",
            "| Cases ({task.completed} / {task.total})",
            BarColumn(30),
            TimeRemainingColumn(),
            transient=True,
            console=console
    ) as progress:
        task = progress.add_task("Format", total=len(cases))
        for i, c in enumerate(cases):
            progress.update(task, advance=1)
            cases[i]['parties'] = format_parties(cases[i]['docname'])
            cases[i]['__conclusion'] = cases[i]['conclusion']
            cases[i]['conclusion'] = format_conclusion(c['__conclusion'])
            cases[i]['__articles'] = cases[i]['article']
            cases[i]['article'] = format_article(cases[i]['__articles'])
            cases[i]['paragraphs'] = format_subarticle(cases[i]['__articles'])
            cases[i]['externalsources'] = cases[i]["externalsources"].split(';') if len(
                cases[i]['externalsources']) > 0 else []
            cases[i]["documentcollectionid"] = cases[i]["documentcollectionid"].split(';') if len(
                cases[i]['documentcollectionid']) > 0 else []
            cases[i]["issue"] = cases[i]["issue"].split(';') if len(cases[i]['issue']) > 0 else []
            cases[i]["representedby"] = cases[i]["representedby"].split(';') if len(
                cases[i]['representedby']) > 0 else []
            cases[i]["extractedappno"] = cases[i]["extractedappno"].split(';')

            cases[i]['externalsources'] = [e.strip() for e in cases[i]['externalsources']]
            cases[i]['documentcollectionid'] = [e.strip() for e in cases[i]['documentcollectionid']]
            cases[i]['issue'] = [e.strip() for e in cases[i]['issue']]
            cases[i]['representedby'] = [e.strip() for e in cases[i]['representedby']]
            cases[i]['extractedappno'] = [e.strip() for e in cases[i]['extractedappno']]

            cases[i]['country'] = COUNTRIES[cases[i]['respondent'].split(';')[0]]
            cases[i]['originatingbody_type'] = ORIGINATING_BODY[cases[i]['originatingbody']]['type']
            cases[i]['originatingbody_name'] = ORIGINATING_BODY[cases[i]['originatingbody']]['name']

            cases[i]["rank"] = cases[i]['Rank']
            del cases[i]["Rank"]

            del cases[i]["isplaceholder"]
            cases[i]["kpdate"] = cases[i]['kpdateAsText']
            del cases[i]['kpdateAsText']
            del cases[i]["documentcollectionid2"]
            cases[i]["kpthesaurus"] = cases[i]["kpthesaurus"].split(';')
            cases[i]["scl"] = cases[i]["scl"].split(';') if cases[i]["scl"].strip() else []
            del cases[i]["doctype"]
            del cases[i]["meetingnumber"]
    print(TAB + "> Format case [green][DONE]")
    return cases


def filter_cases(cases):
    """
        Filter the list of cases.

        :param cases: list of cases
        :type cases: [dict]
        :return: filtered list of cases
        :rtype: [dict]
    """
    total = len(cases)
    print(TAB + '> Total number of cases before filtering: {}'.format(total))
    if total == 0:
        print(TAB + '[bold red]:double_exclamation_mark: There is no case to filter!')
        exit(1)
    print(TAB + '> Remove non-english cases')
    cases = [i for i in cases if i["languageisocode"] == "ENG"]
    print(TAB + '  ⮡ Remaining: {} ({:.4f}%)'.format(len(cases), 100 * float(len(cases)) / total))
    print(TAB + '> Keep only cases with a judgment document:')
    cases = [i for i in cases if i["doctype"] == "HEJUD"]
    print(TAB + '  ⮡ Remaining: {} ({:.4f}%)'.format(len(cases), 100 * float(len(cases)) / total))
    # print(' - Remove cases without an attached document:')
    # cases = [i for i in cases if i["application"].startswith("MS WORD")]
    # print('\tRemaining: {} ({}%)'.format(len(cases), 100 * float(len(cases)) / total ))
    print(TAB + '> Keep cases with a clear conclusion:')
    cases = [i for i in cases if
             "No-violation" in i["conclusion"] or "No violation" in i["conclusion"] or "Violation" in i[
                 "conclusion"] or "violation" in i["conclusion"]]
    print(TAB + '  ⮡ Remaining: {} ({:.4f}%)'.format(len(cases), 100 * float(len(cases)) / total))
    print(TAB + '> Remove a specific list of cases hard to process:')
    cases = [i for i in cases if i['itemid'] not in ["001-154354", "001-108395", "001-79411"]]
    print(TAB + '  ⮡ Remaining: {} ({:.4f}%)'.format(len(cases), 100 * float(len(cases)) / total))
    print(TAB + '-' * 50)
    print(TAB + '> Final number of cases: {}'.format(len(cases)))
    return cases


def generate_statistics(cases):
    """
        Generate statistics about the cases

        :param cases: list of cases
        :type cases: [dict]
        :return: statistics about the cases
        :rtype: dict
    """

    def generate_count(k, cases):
        s = []
        for c in cases:
            if k == 'conclusion':
                # We do not take into account mention and details
                s.extend([a['element'] for a in c[k]])
            else:
                if isinstance(c[k], list):
                    if len(c[k]):
                        s.extend(c[k])
                elif isinstance(c[k], str):  # string
                    if len(c[k].strip()):
                        s.append(c[k])
        return s

    table = Table()
    table.add_column("Attribute", style="cyan", no_wrap=True)
    table.add_column("Cardinal", justify="right", style="magenta")
    table.add_column("Density", justify="right", style="green")

    keys = cases[0].keys()
    except_k = []
    stats = {'attributes': {}}
    for k in [i for i in keys if i not in except_k]:
        s = generate_count(k, cases)
        s = Counter(s)
        stats['attributes'][k] = {
            'cardinal': len(s),
            'density': float(len(s)) / len(cases)
        }
        table.add_row(k, str(len(s)), '{:.4f}'.format(float(len(s)) / len(cases)))
    print(table)
    return stats


def run(console, build, title, force=False):
    __console = console
    global print
    print = __console.print

    print(Markdown("- **Step configuration**"))
    input_folder = os.path.join(build, 'raw', 'raw_cases_info')
    output_folder = path.join(build, 'raw', 'cases_info')
    print(TAB + '> Step folder: {}'.format(path.join(build, 'cases_info')))
    make_build_folder(console, output_folder, force, strict=False)

    cases = []
    files = [path.join(input_folder, f) for f in listdir(input_folder) if path.isfile(path.join(input_folder, f)) if
             '.json' in f]
    for p in files:
        try:
            with open(p, 'r') as f:
                content = f.read()
                index = json.loads(content)
                cases.extend(index["results"])
        except Exception as e:
            log.info(p, e)
    cases = [c["columns"] for c in cases]

    print(Markdown("- **Filter cases**"))
    cases = filter_cases(cases)
    print(Markdown("- **Format cases metadata**"))
    cases = format_cases(console, cases)

    print(Markdown("- **Generate statistics**"))
    stats = generate_statistics(cases)

    with open(path.join(output_folder, 'filter.statistics.json'), 'w') as outfile:
        json.dump(stats, outfile, indent=4, sort_keys=True)

    with open(path.join(output_folder, 'raw_cases_info_all.json'), 'w') as outfile:
        json.dump(cases, outfile, indent=4, sort_keys=True)

    filtered_cases = []
    for c in cases:
        classes = []
        for e in c['conclusion']:
            if e['type'] in ['violation', 'no-violation']:
                if 'article' in e:
                    g = e['article']
                    classes.append('{}:{}'.format(g, 1 if e['type'] == 'violation' else 0))

        classes = list(set(classes))
        opposed_classes = any(
            [e for e in classes if e.split(':')[0] + ':' + str(abs(1 - int(e.split(':')[-1]))) in classes])
        if len(classes) > 0 and not opposed_classes:
            filtered_cases.append(c)

    outcomes = {}
    cases_per_articles = {}
    for c in filtered_cases:
        ccl = c['conclusion']
        for e in ccl:
            if e['type'] in ['violation', 'no-violation']:
                if 'article' in e:
                    if e['article'] not in outcomes:
                        outcomes[e['article']] = {
                            'violation': 0,
                            'no-violation': 0,
                            'total': 0
                        }
                    outcomes[e['article']][e['type']] += 1
                    outcomes[e['article']]['total'] += 1
                    if e['article'] not in cases_per_articles:
                        cases_per_articles[e['article']] = []
                    cases_per_articles[e['article']].append(c)

    print(Markdown("- **Generate case listing for datasets**"))
    multilabel_cases = []
    multilabel_index = set()
    with Progress(
            TAB + "> Generate case info for specific article [IN PROGRESS]",
            "| {task.fields[progress_array]}",
            transient=True,
            console=console
    ) as progress:
        progress_array = []

        def to_str(a):
            if len(a) == 1:
                return '[[green]{}[white]]'.format(a[0])
            return '[{}{}]'.format(''.join(['[green]{}[white], '.format(e) for e in a[:-1]]), a[-1])

        task = progress.add_task("Generate datasets cases", total=len(outcomes), progress_array="[]")
        for k in outcomes.keys():
            progress_array.append(k)
            with open(path.join(output_folder, 'raw_cases_info_article_{}.json'.format(k)), 'w') as outfile:
                json.dump(cases_per_articles[k], outfile, indent=4, sort_keys=True)
            multilabel_cases.extend(cases_per_articles[k])
            for c in cases_per_articles[k]:
                multilabel_index.add(c['itemid'])
            progress.update(task, advance=1, progress_array=to_str(progress_array))
    print(TAB + "> Generate case info for specific article [green][DONE]", )
    multilabel_cases_unique = []
    for c in multilabel_cases:
        if c['itemid'] in multilabel_index:
            multilabel_cases_unique.append(c)
            multilabel_index.discard(c['itemid'])

    with open(path.join(output_folder, 'raw_cases_info_multilabel.json'), 'w') as outfile:
        json.dump(multilabel_cases_unique, outfile, indent=4, sort_keys=True)
    print(TAB + "> Generate case info for multilabel dataset [green][DONE]", )
    multiclass_index = {}  # Key: case ID / Value = number of different dataset it appears in
    multiclass_cases = []
    sorted_outcomes = dict(sorted(outcomes.items(), key=lambda x: x[1]['total'])).keys()
    for k in sorted_outcomes:
        for c in cases_per_articles[k]:
            if c['itemid'] not in multiclass_index:
                nb_datasets = [e['article'] for e in c['conclusion'] if 'article' in e]
                if len(list(set(nb_datasets))) == 1:
                    for cc in c['conclusion']:
                        if 'article' in cc and cc['article'] == k:
                            c['mc_conclusion'] = [cc]
                            break
                    if 'mc_conclusion' in c:
                        multiclass_index[c['itemid']] = k
                        multiclass_cases.append(c)
                    else:
                        log.info('No article found for {}'.format(c['itemid']))
                else:

                    log.info('Article {} in {} datasets: {}. Skip for multiclass.'.format(c['itemid'],
                                                                                          len(set(nb_datasets)),
                                                                                          ','.join(
                                                                                              list(set(nb_datasets))))
                             )

    with open(path.join(output_folder, 'raw_cases_info_multiclass.json'), 'w') as outfile:
        json.dump(multiclass_cases, outfile, indent=4, sort_keys=True)
    print(TAB + "> Generate case info for multiclass [green][DONE]", )


def main(args):
    console = Console(record=True)
    run(console, args.build, args.title, args.force)


def parse_args(parser):
    args = parser.parse_args()
    return args


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter and format ECHR cases information')
    parser.add_argument('--build', type=str, default="./build/echr_database/")
    parser.add_argument('--title', type=str)
    parser.add_argument('-f', action='store_true')
    args = parse_args(parser)

    main(args)
