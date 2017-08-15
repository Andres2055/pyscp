#!/usr/bin/env python3

"""

Clases Base de Abstracción

pyscp builds most of its functionality on top of three large classes: Wiki,
Page, and Thread. This module contains the abstract base classes for those
three. The ABC-s define the abstact methods that each child must implement,
as well as some common functionality that builds on top of the abstract
methods.

Each class inheriting from the ABC-s must implement its own realization of
the abstract methods, and can also provide additional methods unique to it.

This module also defines the named tuples for simple containers used by the
three core classes, such as Revision or Vote.
"""


###############################################################################
# Modulos Importados
###############################################################################

import abc
import arrow
import bs4
import collections
import functools
import itertools
import logbook
import re
import urllib.parse

import pyscp.utils

###############################################################################
# Constantes Globales Y Variables
###############################################################################

logbook.FileHandler('pyscp.log').push_application()
log = logbook.Logger(__name__)

###############################################################################
# Clases Base de Abstracción
###############################################################################


class Page(metaclass=abc.ABCMeta):
    """
    Page Abstract Base Class.

    Page object are wrappers around individual wiki-pages, and allow simple
    operations with them, such as retrieving the rating or the author.

    Each Page instance is attached to a specific instance of the Wiki class.
    The wiki may be used by the page to retrieve a list of titles or other
    similar wiki-wide information that may be used by the Page to, in turn,
    deduce some information about itself.

    Typically, the Page instances should not be created directly. Instead,
    calling an instance of a Wiki class will creating a Page instance
    attached to that wiki.
    """

    ###########################################################################
    # Special Methods
    ###########################################################################

    def __init__(self, wiki, url):
        self.url = url
        self._wiki = wiki

    def __repr__(self):
        return '{}.{}({}, {})'.format(
            self.__module__, self.__class__.__name__,
            repr(self.url), repr(self._wiki))

    def __eq__(self, other):
        if not hasattr(other, 'url') or not hasattr(other, '_wiki'):
            return False
        return self.url == other.url and self._wiki is other._wiki

    ###########################################################################
    # Abstract Methods
    ###########################################################################

    @property
    @abc.abstractmethod
    def _pdata(self):
        """
		Comunmente usa datos sobre la página.

        This method should return a tuple, the first three elements of which
        are the id number of the page; the id number of the page's comments
        thread; and the html contents of the page.

        Any additional elements of the tuple are left to the discretion
        of the individual Page implimentations.
        """
        pass

    @property
    @abc.abstractmethod
    def history(self):
        """
		Historial de revisión de la página

        Debe retornar un lista ordenada de tuplas nombrando la Revision.
        """
        pass

    @property
    @abc.abstractmethod
    def votes(self):
        """
        Votos de página.

        Debe retornar una lista de tuplas nombrando Votos.
        """
        pass

    @property
    @abc.abstractmethod
    def tags(self):
        """
        Etiquétas de la Página

        Debe retornar un set de una cadena de texto.
        """
        pass

    ###########################################################################
    # Internal Methods
    ###########################################################################

    @property
    def _id(self):
        """Unico número ID de la página."""
        return self._pdata[0]

    @pyscp.utils.cached_property
    def _thread(self):
        """Objeto Thread correspondiente a las páginas de hilos de comentarios."""
        return self._wiki.Thread(self._wiki, self._pdata[1])

    @property
    def _raw_title(self):
        """Título como es mostrado en la página."""
        title = self._soup.find(id='page-title')
        return title.text.strip() if title else ''

    @property
    def _raw_author(self):
        return self.history[0].user

    @property
    def _soup(self):
        """BeautifulSoup del contenido de la página."""
        return bs4.BeautifulSoup(self.html, 'lxml')

    ###########################################################################
    # Properties
    ###########################################################################

    @property
    def html(self):
        """Contendio HTML de la página."""
        return self._pdata[2]

    @property
    def posts(self):
        """Lista de comentarios hechos en la página."""
        return self._thread.posts

    @property
    def comments(self):
        """Alias para Page.posts."""
        return self._thread.posts

    @property
    def text(self):
        """Texto llano de la página."""
        return self._soup.find(id='page-content').text

    @property
    def wordcount(self):
        """Número de palabras encontrados en la página."""
        return len(re.findall(r"[\w'█_-]+", self.text))

    @property
    def images(self):
        """Número de imagenes mostradas en la página."""
        # TODO: needs more work.
        return [i['src'] for i in self._soup('img')]

    @property
    def name(self):
        return self.url.split('/')[-1]

    @property
    def title(self):
        """
        Título de la página.

        En caso de los artículos SCP, incluirá el título de la página de la 'serie.'
        """
        try:
            return '{}: {}'.format(
                self._raw_title, self._wiki.titles()[self.url])
        except KeyError:
            return self._raw_title

    @property
    def created(self):
        """Cuando creas la página."""
        return self.history[0].time

    @property
    def metadata(self):
        """
        Retorna la página de metadatos

	Autores en este caso incluye a todos los usuarios relacionados
	a la creación y subsecuente mantenimiento de la página. Los
	valores en el dict describe a los usuarios relacionados con la página.
        """
        data = [i for i in self._wiki.metadata() if i.url == self.url]
        data = {i.user: i for i in data}

        if 'autor' not in {i.role for i in data.values()}:
            meta = Metadata(self.url, self._raw_author, 'autor', None)
            data[self._raw_author] = meta

        for k, v in data.items():
            if v.role == 'autor' and not v.date:
                data[k] = v._replace(date=self.created)

        return data

    @property
    def rating(self):
        """Puntaje de la página, excluyendo a los usuarios eliminados."""
        return sum(
            v.value for v in self.votes if v.user != '(account deleted)')

    @property
    @pyscp.utils.listify()
    def links(self):
        """
		Otras páginas enlazadas desde esta.

        Retorna una lista ordenada de urls unicas. Enlaces fuera del sitio o
		enlaces a imagenes no son incluidos.
        """
        unique = set()
        for element in self._soup.select('#page-content a'):
            href = element.get('href', None)
            if (not href or href[0] != '/' or  # malo o enlace absoluto
                    href[-4:] in ('.png', '.jpg', '.gif')):
                continue
            url = self._wiki.site + href.rstrip('|')
            if url not in unique:
                unique.add(url)
                yield url

    @property
    def parent(self):
        """Padre de la página actual."""
        if not self.html:
            return None
        breadcrumb = self._soup.select('#breadcrumbs a')
        if breadcrumb:
            return self._wiki.site + breadcrumb[-1]['href']

    @property
    def is_mainlist(self):
        """
        Indica si la página está en la lista principal de artículos SCP.

        Esta es una propiedad exclusiva de lafundacionscp.
        """
        if 'lafundacionscp' not in self._wiki.site:
            return False
        if 'scp' not in self.tags:
            return False
        return bool(re.search(r'/scp-[0-9]{3,4}$', self.url))

    ###########################################################################
    # Metodos
    ###########################################################################

    def build_attribution_string(
            self, templates=None, group_templates=None, separator=', ',
            user_formatter=None):
        """
        Crea un string de atribución basado en los metadatos de las páginas.

        Esta es una operación comunmente necesitada. The result should be a nicely
        formatted, human-readable description of who was and is involved with
        the page, and in what role.
        """
        roles = 'autor reescritor traductor mantenimiento'.split()

        if not templates:
            templates = {i: '{{user}} ({})'.format(i) for i in roles}

        items = list(self.metadata.values())
        items.sort(key=lambda x: [roles.index(x.role), x.date])

        # grupo de usuarios con el mismo rol en conjunto a la misma fecha == group users in the same role on the same date together
        itemdict = collections.OrderedDict()
        for i in items:
            user = user_formatter.format(i.user) if user_formatter else i.user
            key = (i.role, i.date)
            itemdict[key] = itemdict.get(key, []) + [user]

        output = []

        for (role, date), users in itemdict.items():

            hdate = arrow.get(date).humanize() if date else ''

            if group_templates and len(users) > 1:
                output.append(
                    group_templates[role].format(
                        date=date,
                        hdate=hdate,
                        users=', '.join(users[:-1]),
                        last_user=users[-1]))
            else:
                for user in users:
                    output.append(
                        templates[role].format(
                            date=date, hdate=hdate, user=user))

        return separator.join(output)


class Thread(metaclass=abc.ABCMeta):
    """
    Clase Base de Abstracción de Hilos.

    Objetos Hilos representan hilos de foro individuales. Muchas página tienen 
    su hilo de comentarios correspondiente, accesible via Page._thread.
    """

    def __init__(self, wiki, _id, title=None, description=None):
        self._wiki = wiki
        self._id, self.title, self.description = _id, title, description

    @abc.abstractmethod
    def posts(self):
        """Posts en el hilo."""
        pass


class Wiki(metaclass=abc.ABCMeta):
    """
    Clase Base de Abstracción de la Wiki.

    Objetos Wiki proveen completa funcionalidad de la wiki no limitada a páginas
    individuales o hilos.
    """

    ###########################################################################
    # Atributos de Clase
    ###########################################################################

    # Debe apuntar a la respectivas clases Page y Thread en cada submodulo.

    Page = Page
    Thread = Thread

    ###########################################################################
    # Metodos Especiales
    ###########################################################################

    def __init__(self, site):
        parsed = urllib.parse.urlparse(site)
        netloc = parsed.netloc if parsed.netloc else parsed.path
        if '.' not in netloc:
            netloc += '.wikidot.com'
        self.site = urllib.parse.urlunparse(['http', netloc, '', '', '', ''])
        self._title_data = {}

    def __call__(self, name):
        url = name if self.site in name else '{}/{}'.format(self.site, name)
        url = url.replace(' ', '-').replace('_', '-').lower()
        return self.Page(self, url)

    ###########################################################################

    @functools.lru_cache(maxsize=1)
    def metadata(self):
        """
        List page ownership metadata.

        Este método es exclusivo de lafundacionscp, y es usado to fine-tune
        the page ownership information beyond what is possible with Wikidot.
        This allows a single page to have an author different from the user
        who created the zeroth revision of the page, or even have multiple
        users attached to the page in various roles.
        """
        if 'lafundacionscp' not in self.site:
            return []
        soup = self('attribution-metadata')._soup
        results = []
        for row in soup('tr')[1:]:
            name, user, type_, date = [i.text.strip() for i in row('td')]
            name = name.lower()
            url = '{}/{}'.format(self.site, name)
            results.append(pyscp.core.Metadata(url, user, type_, date))
        return results

    def _update_titles(self):
        for name in (
                'serie-scp-i', 'serie-scp-ii', 'serie-scp-iii', 'serie-scp-iv', 'serie-scp-es'
                'scps-humoristicos', 'scp-ex', 'scps-archivados'):
            page = self(name)
            try:
                soup = page._soup
            except:
                continue
            self._title_data[name] = soup

    @pyscp.utils.ignore(value={})
    @pyscp.utils.log_errors(logger=log.error)
    @functools.lru_cache(maxsize=1)
    def titles(self):
        """Diccionario de pareja url/título para artículos SCP."""
        if 'lafundacionscp' not in self.site:
            return {}

        self._update_titles()

        elems = [i.select('ul > li') for i in self._title_data.values()]
        elems = list(itertools.chain(*elems))
        try:
            elems += list(self('scp-001')._soup(class_='series')[1]('p'))
        except:
            pass

        titles = {}
        for elem in elems:

            sep = ' - ' if ' - ' in elem.text else ', '
            try:
                url1 = self.site + elem.a['href']
                skip, title = elem.text.split(sep, maxsplit=1)
            except (ValueError, TypeError):
                continue

            if title != '[ACCESO DENEGADO]':
                url2 = self.site + '/' + skip.lower()
                titles[url1] = titles[url2] = title

        return titles

    def list_pages(self, **kwargs):
        """Retorna páginas relacionadas al criterio especificado."""
        pages = self._list_pages_parsed(**kwargs)
        author = kwargs.pop('autor', None)
        if not author:
            # si 'autor' no es especificado, there's no need to check rewrites
            return pages
        include, exclude = set(), set()
        for meta in self.metadata():
            if meta.user == author:
                # si empareja el nombre de usuario, incluyelo sin tener en cuenta el tipo
                include.add(meta.url)
            elif meta.role == 'autor':
                # excluye solo si la reescritura de tipo es autor. 
                # si la url tiene al autor y al reescritor,
                # ambos apareceran en list_pages.
                exclude.add(meta.url)
        urls = {p.url for p in pages} | include - exclude
        # if no other options beside author were specified,
        # just return everything we can
        if not kwargs:
            return map(self, sorted(urls))
        # otherwise, retrieve the list of urls without the author parameter
        # to check which urls we should return and in which order
        pages = self._list_pages_parsed(**kwargs)
        return [p for p in pages if p.url in urls]

###############################################################################
# Contenedores de Tuplas Nombradas
###############################################################################

nt = collections.namedtuple
Revision = nt('Revision', 'id number user time comment')
Vote = nt('Vote', 'user value')
Post = nt('Post', 'id title content user time parent')
File = nt('File', 'url name filetype size')										   
Metadata = nt('Metadata', 'url user role date')
Category = nt('Category', 'id title description size')
Image = nt('Image', 'url source status notes data')
del nt
