#!/usr/bin/env python3

"""
Clases de acceso a Snapshot.

Este módulo contiene las clases para facilitar la extracción de información
y comunicación con el sqlite Snapshots.
"""

###############################################################################
# Modulos Importados
###############################################################################

import bs4
import concurrent.futures
import functools
import itertools
import logging
import operator
import pathlib
import re
import requests

from pyscp import core, orm, utils

###############################################################################
# Constantes Globales Y Variables
###############################################################################

log = logging.getLogger(__name__)

###############################################################################


class Page(core.Page):
    """Objeto Page."""

    ###########################################################################
    # Métodos Internos
    ###########################################################################

    def _query(self, ptable, stable='User'):
        """Genera consultas SQL usando los datos recabados."""
        pt, st = [getattr(orm, i) for i in (ptable, stable)]
        return pt.select(pt, st.name).join(st).where(pt.page == self._id)

    @utils.cached_property
    def _pdata(self):
        """#preload Precarga las id's y el contenido de la página."""
        pdata = orm.Page.get(orm.Page.url == self.url)
        return pdata.id, pdata._data['thread'], pdata.html

    ###########################################################################
    # Propiedades
    ###########################################################################

    @property
    def html(self):
        """Retorna el contenido HTML de la página."""
        return self._pdata[2]

    @utils.cached_property
    def history(self):
        """Retorna las revisiones de la página."""
        revs = self._query('Revision')
        revs = sorted(revs, key=lambda x: x.number)
        return [core.Revision(
                r.id, r.number, r.user.name, str(r.time), r.comment)
                for r in revs]

    @utils.cached_property
    def votes(self):
        """Retorna todos los votos hechos en la página."""
        return [core.Vote(v.user.name, v.value)
                for v in self._query('Vote')]

    @utils.cached_property
    def tags(self):
        """Retorna el juego de etiquétas con las que la página fue etiquetada."""
        return {pt.tag.name for pt in self._query('PageTag', 'Tag')}


class Thread(core.Thread):
    """Hilos de/l Discusión/foro."""

    @utils.cached_property
    def posts(self):
        """Objetos Post pertenecientes a este hilo."""
        fp = orm.ForumPost
        us = orm.User
        query = fp.select(fp, us.name).join(us).where(fp.thread == self._id)
        return [core.Post(
                p.id, p.title, p.content, p.user.name,
                str(p.time), p._data['parent'])
                for p in query]


class Wiki(core.Wiki):
    """Snapshot de un sitio web Wikidot."""

    Page = Page
    Thread = Thread
    # Tautology = Tautology

    ###########################################################################
    # Métodos Especiales
    ###########################################################################

    def __init__(self, site, dbpath):
        """Crea un instancia wiki."""
        super().__init__(site)
        if not pathlib.Path(dbpath).exists():
            raise FileNotFoundError(dbpath)
        self.dbpath = dbpath
        orm.connect(dbpath)

    def __repr__(self):
        """Impresión bonita en la instancia actual."""
        return '{}.{}({}, {})'.format(
            self.__module__,
            self.__class__.__qualname__,
            repr(self.site),
            repr(self.dbpath))

    ###########################################################################
    # Métodos Internos
    ###########################################################################

    @staticmethod
    def _filter_author(author):
        return (orm.Page.select(orm.Page.url)
                .join(orm.Revision).join(orm.User)
                .where(orm.Revision.number == 0)
                .where(orm.User.name == author))

    @staticmethod
    def _filter_tag(tag):
        return (orm.Page.select(orm.Page.url)
                .join(orm.PageTag).join(orm.Tag)
                .where(orm.Tag.name == tag))

    @staticmethod
    def _get_operator(string):
        symbol, *values = re.split(r'(\d+)', string)
        opdict = {
            '>': 'gt', '<': 'lt', '>=': 'ge', '<=': 'le', '=': 'eq', '': 'eq'}
        if symbol not in opdict:
            raise ValueError
        return getattr(operator, opdict[symbol]), values

    def _filter_rating(self, rating):
        compare, values = self._get_operator(rating)
        rating = int(values[0])
        return (orm.Page.select(orm.Page.url)
                .join(orm.Vote).group_by(orm.Page.url)
                .having(compare(orm.peewee.fn.sum(orm.Vote.value), rating)))

    def _filter_created(self, created):
        compare, values = self._get_operator(created)
        date = '-'.join(values[::2])
        return (orm.Page.select(orm.Page.url)
                .join(orm.Revision).where(orm.Revision.number == 0)
                .group_by(orm.Page.url)
                .having(compare(
                    orm.peewee.fn.substr(orm.Revision.time, 1, len(date)),
                    date)))

    def _list_pages_parsed(self, **kwargs):
        query = orm.Page.select(orm.Page.url)
        keys = ('author', 'tag', 'rating', 'created')
        keys = [k for k in keys if k in kwargs]
        for k in keys:
            query = query & getattr(self, '_filter_' + k)(kwargs[k])
        if 'limit' in kwargs:
            query = query.limit(kwargs['limit'])
        return map(self, [p.url for p in query])

    ###########################################################################
    # Métodos Específicos Para La Wiki SCP
    ###########################################################################

    @functools.lru_cache(maxsize=1)
    def list_images(self):
        """Metadatos de imagen."""
        query = (
            orm.Image.select(orm.Image, orm.ImageStatus.name)
            .join(orm.ImageStatus))
        return [core.Image(r.url, r.source, r.status.name, r.notes, r.data)
                for r in query]

###############################################################################


class SnapshotCreator:
    """
    Create a snapshot of a wikidot site.

    This class uses WikidotConnector to iterate over all the pages of a site,
    and save the html content, revision history, votes, and the discussion
    of each to a sqlite database. Optionally, standalone forum threads can be
    saved too.

    In case of the scp-wiki, some additional information is saved:
    images for which their CC status has been confirmed, and info about
    overwriting page authorship.

    In general, this class will not save images hosted on the site that is
    being saved. Only the html content, discussions, and revision/vote
    metadata is saved.
    """

    def __init__(self, dbpath):
        """Crea una instancia."""
        if pathlib.Path(dbpath).exists():
            raise FileExistsError(dbpath)
        orm.connect(dbpath)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)

    def take_snapshot(self, wiki, forums=False):
        """Asume un nuevo snapshot."""
        self.wiki = wiki
        self._save_all_pages()
        if forums:
            self._save_forums()
        if 'lafundacionscp' in self.wiki.site:
            self._save_meta()
        orm.queue.join()
        self._save_cache()
        orm.queue.join()
        log.info('Snapshot succesfully taken.')

    def _save_all_pages(self):
        """Itera sobre las páginas del sitio, llama a _save_page para cada uno."""
        orm.create_tables(
            'Page', 'Revision', 'Vote', 'ForumPost',
            'PageTag', 'ForumThread', 'User', 'Tag')
        count = next(
            self.wiki.list_pages(body='total', limit=1))._body['total']
        bar = utils.ProgressBar('Guardando Página'.ljust(20), int(count))
        bar.start()
        for _ in self.pool.map(self._save_page, self.wiki.list_pages()):
            bar.value += 1
        bar.stop()

    @utils.ignore(requests.HTTPError)
    def _save_page(self, page):
        """Descarga contenidos, revisiones, votos y discusiones de la página."""
        orm.Page.create(
            id=page._id, url=page.url, thread=page._thread._id, html=page.html)

        revisions = orm.User.convert_to_id(i._asdict() for i in page.history)
        votes = orm.User.convert_to_id(i._asdict() for i in page.votes)
        tags = [{'tag': t} for t in page.tags]
        tags = orm.Tag.convert_to_id(tags, key='tag')

        def _insert(table, data):
            table.insert_many(dict(i, page=page._id) for i in data)

        _insert(orm.Revision, revisions)
        _insert(orm.Vote, votes)
        _insert(orm.PageTag, tags)

        self._save_thread(page._thread)

    def _save_forums(self):
        """Descarga y salva los hilos autónomos del foro."""
        orm.create_tables(
            'ForumPost', 'ForumThread', 'ForumCategory', 'User')
        cats = self.wiki.list_categories()
        cats = [i for i in cats if i.title != 'Per page discussions']
        orm.ForumCategory.insert_many(dict(
            id=c.id,
            title=c.title,
            description=c.description) for c in cats)
        total_size = sum(c.size for c in cats)
        bar = utils.ProgressBar('GUARDANDO HILO DEL FORO', total_size)
        bar.start()
        for cat in cats:
            threads = set(self.wiki.list_threads(cat.id))
            c_id = itertools.repeat(cat.id)
            for _ in self.pool.map(self._save_thread, threads, c_id):
                bar.value += 1
        bar.stop()

    def _save_thread(self, thread, c_id=None):
        orm.ForumThread.create(
            category=c_id, id=thread._id,
            title=thread.title, description=thread.description)
        posts = orm.User.convert_to_id(map(vars, thread.posts))
        orm.ForumPost.insert_many(
            dict(p, thread=thread._id) for p in posts)

    def _save_meta(self):
        orm.create_tables(
            'Image', 'ImageStatus')
        licenses = {
            'PERMISO GARANTIZÁDO', 'BY-NC-SA CC', 'BY-SA CC', 'DOMINIO PÚBLICO'}
        images = [i for i in self.wiki.list_images() if i.status in licenses]
        self.ibar = utils.ProgressBar(
            'GUARDANDO IMÁGEN'.ljust(20), len(images))
        self.ibar.start()
        data = list(self.pool.map(self._save_image, images))
        self.ibar.stop()
        images = orm.ImageStatus.convert_to_id(
            [i._asdict() for i in images], key='status')
        orm.Image.insert_many(
            dict(i, data=d) for i, d in zip(images, data) if d)

    @utils.ignore(requests.RequestException)
    def _save_image(self, image):
        self.ibar.value += 1
        if not image.source:
            log.info('Dirección de la imágen no especificado: ' + image.url)
            return
        return self.wiki.req.get(image.url, allow_redirects=True).content

    def _save_cache(self):
        for table in orm.User, orm.Tag, orm.OverrideType, orm.ImageStatus:
            if hasattr(table, '_id_cache') and table._id_cache:
                table.write_ids('name')
