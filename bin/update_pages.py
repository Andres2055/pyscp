#!/usr/bin/env python3

"""
Update wiki pages.

This script is used to update scp-wiki tale hubs and other such pages.
"""

###############################################################################
# Module Imports
###############################################################################

import arrow
import collections
import logging
import pyscp
import re
import string

###############################################################################

log = logging.getLogger('pyscp')

###############################################################################

TEMPLATE = """
[[# {name}]]
[[div class="section"]]
+++ {disp}
[#top ⇑]
{header}
{body}
[[/div]]

"""

###############################################################################


class Updater:

    def __init__(self, wiki, pages):
        self.wiki = wiki
        self.pages = pages

    def disp(self):
        return self.keys()

    def get_author(self, page):
        return page.build_attribution_string(
            user='[[user {}]]', separator=' _\n')

    def get_section(self, idx):
        name = self.keys()[idx]
        disp = self.disp()[idx]
        pages = [p for p in self.pages if self.keyfunc(p) == name]

        if pages:
            body = '\n'.join(map(
                self.format_page, sorted(pages, key=self.sortfunc)))
        else:
            body = self.NODATA

        return TEMPLATE.format(
            name=name.replace(' ', '-'),
            disp=disp,
            header=self.HEADER,
            body=body)

    def update(self, *targets):
        output = ['']
        for idx in range(len(self.keys())):
            section = self.get_section(idx)
            if len(output[-1]) + len(section) < 180000:
                output[-1] += section
            else:
                output.append(section)
        for idx, target in enumerate(targets):
            source = output[idx] if idx < len(output) else ''
            self.wiki(target).revert(0)
            self.wiki(target).edit(source, comment='Actualización automática')
            log.info('{} {}'.format(target, len(source)))

###############################################################################


class TaleUpdater(Updater):

    HEADER = '||~ Título||~ Autor||~ Creado||'
    NODATA = '||||||= **NO DATA AVAILABLE**||'

    def format_page(self, page=None):
        return '||[[[{}|]]]||{}||//{}//||\n||||||{}||'.format(
            page._body['fullname'], self.get_author(page),
            page.created[:10], page._body['preview'])

    def update(self, target):
        targets = [
            'component:tales-by-{}-{}'.format(target, i + 1) for i in range(4)]
        super().update(*targets)


class TalesByTitle(TaleUpdater):

    def keys(self):
        return list(string.ascii_uppercase) + ['misc']

    def keyfunc(self, page):
        l = page._body['title'][0]
        return l.upper() if l.isalpha() else 'misc'

    def sortfunc(self, page):
        return page._body['title'].lower()


class TalesByAuthor(TaleUpdater):

    def keys(self):
        return sorted(list(string.ascii_uppercase) + ['Dr', 'misc'])

    def keyfunc(self, page):
        templates = collections.defaultdict(lambda: '{user}')
        authors = page.build_attribution_string(templates).split(', ')
        author = authors[0]
        if re.match(r'Dr[^a-z]|Doctor|Doc[^a-z]', author):
            return 'Dr'
        elif author[0].isalpha():
            return author[0].upper()
        else:
            return 'misc'

    def sortfunc(self, page):
        author = sorted(page.metadata.keys())[0]
        return author.lower()


class TalesByDate(TaleUpdater):

    def disp(self):
        return [
            arrow.get(i, 'AAAA-MM').format('MMMM AAAA') for i in self.keys()]

    def keys(self):
        return [i.format('AAAA-MM') for i in
                arrow.Arrow.range('month', arrow.get('2008-07'), arrow.now())]

    def keyfunc(self, page=None):
        return page.created[:7]

    def sortfunc(self, page):
        return page.created


def update_tale_hubs(wiki):
    pages = list(wiki.list_pages(
        tags='relato -hub -_sys',
        body='title created_by created_at preview tags'))
    TalesByTitle(wiki, pages).update('title')
    TalesByAuthor(wiki, pages).update('author')
    TalesByDate(wiki, pages).update('date')

###############################################################################


class CreditUpdater(Updater):

    HEADER = ''
    NODATA = '||||= **DATOS NO DISPONIBLES**||'

    def format_page(self, page):
        return '||[[[{}|{}]]]||{}||'.format(
            page._body['fullname'],
            page.title.replace('[', '').replace(']', ''),
            self.get_author(page))

    def sortfunc(self, page):
        title = []
        for word in re.split('([0-9]+)', page._body['title']):
            if word.isdigit():
                title.append(int(word))
            else:
                title.append(word.lower())
        return title

    def update(self, target):
        super().update('component:creditos-' + target)


class SeriesCredits(CreditUpdater):

    def __init__(self, wiki, pages, series):
        super().__init__(wiki, pages)
        self.series = (series - 1) * 1000

    def keys(self):
        return ['{:03}-{:03}'.format(i or 2, i + 99)
                for i in range(self.series, self.series + 999, 100)]

    def keyfunc(self, page):
        num = re.search('[scp]+-([0-9]+)$', page._body['fullname'])
        if not num:
            return
        num = (int(num.group(1)) // 100) * 100
        return '{:03}-{:03}'.format(num or 2, num + 99)


class MiscCredits(CreditUpdater):

    def __init__(self, wiki, pages):
        self.proposals = pyscp.wikidot.Wiki('lafundacionscp')('scp-es-001').links
        super().__init__(wiki, pages)

    def keys(self):
        return 'propuesta explicado humorístico archivadp'.split()

    def disp(self):
        return [
            'Propuesta 001', 'Fenonemo Explicado',
            'Artículo Humorístico', 'Artículo Archivado']

    def keyfunc(self, page):
        if page.url in self.proposals:
            return 'propuesta'
        for tag in ('explicado', 'humorístico', 'archivado'):
            if tag in page.tags:
                return tag


def update_credit_hubs(wiki):
    pages = list(wiki.list_pages(
        tag='scp', body='title created_by tags'))
    wiki = pyscp.wikidot.Wiki('borradores-scp-es')
    with open('pyscp_bot.pass') as file:
        wiki.auth('Pancho_Bot', file.read())

    SeriesCredits(wiki, pages, 1).update('series1')
    SeriesCredits(wiki, pages, 2).update('series2')
    SeriesCredits(wiki, pages, 3).update('series3')
    MiscCredits(wiki, pages).update('misc')

###############################################################################

wiki = pyscp.wikidot.Wiki('lafundacionscp')
with open('pyscp_bot.pass') as file:
    wiki.auth('Pancho_Bot'), file.read())

pyscp.utils.default_logging()
#update_credit_hubs(wiki)

update_tale_hubs(wiki)
