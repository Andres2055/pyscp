#!/usr/bin/env python3

"""
Wikidot access classes.

Este módulo contiene las clases que facilitan la extracción de información
y la comunación con los sitio hosteados en Wikidot.
"""

###############################################################################
# Módulos Importados
###############################################################################

import arrow
import bs4
import functools
import itertools
import logging
import pyscp
import re
import requests

###############################################################################
# Constantes Globales Y Variables
###############################################################################

log = logging.getLogger(__name__)


###############################################################################
# Clases de Utilidad
###############################################################################

class InsistentRequest(requests.Session):
	"""Realiza un auto-reintento de petición que maneja la perdida de conexión."""

	def __init__(self, max_attempts=10):
		super().__init__()
		self.max_attempts = max_attempts

	def __repr__(self):
		return '{}(max_attempts={})'.format(
			self.__class__.__name__, self.max_attempts)

	def request(self, method, url, **kwargs):
		log.debug('%s: %s %s', method, url, repr(kwargs) if kwargs else '')
		kwargs.setdefault('timeout', 60)
		kwargs.setdefault('allow_redirects', False)
		for _ in range(self.max_attempts):
			try:
				resp = super().request(method=method, url=url, **kwargs)
			except (
					requests.ConnectionError,
					requests.Timeout,
					requests.exceptions.ChunkedEncodingError):
				continue
			if 200 <= resp.status_code < 300:
				return resp
			elif 300 <= resp.status_code < 400:
				raise requests.HTTPError(
					'Redirect attempted with url: {}'.format(url))
			elif 400 <= resp.status_code < 600:
				continue
		raise requests.ConnectionError(
			'Se ha excedido el máximo de reintentos con la url: {}'.format(url))

	def get(self, url, **kwargs):
		return self.request('GET', url, **kwargs)

	def post(self, url, **kwargs):
		return self.request('POST', url, **kwargs)


###############################################################################


class Page(pyscp.core.Page):
	"""Crea un objeto Page."""		
	
	def __init__(self, wiki, url):
		super().__init__(wiki, url)
		self._body = {}

    ###########################################################################
    # Metodos Internos
    ###########################################################################

	def _module(self, *args, **kwargs):
		"""Llama al modulo Wikidot."""
		return self._wiki._module(*args, page_id=self._id, **kwargs)

	def _action(self, event, **kwargs):
		"""Ejecuta WikiPageAction."""
		return self._module(
			'Empty', action='WikiPageAction', event=event, **kwargs)

	def _vote(self, value):
		"""Votar en la página."""
		return self._action(
			'RateAction',
			event='ratePage' if value else 'cancelVote',
			points=value,
			force=True)

	def _flush(self, *names):
		if not hasattr(self, '_cache'):
			return
		self._cache = {k: v for k, v in self._cache.items() if k not in names}

	@pyscp.utils.cached_property
	def _pdata(self):
		data = self._wiki.req.get(self.url).text
		soup = bs4.BeautifulSoup(data, 'lxml')
		return (int(re.search('pageId = ([0-9]+);', data).group(1)),
				parse_element_id(soup.find(id='discuss-button')),
				str(soup.find(id='main-content')),
				{e.text for e in soup.select('.page-tags a')})

	@property
	def _raw_title(self):
		if 'title' in self._body:
			return self._body['title']
		return super()._raw_title

	@property
	def _raw_author(self):
		if 'created_by' in self._body:
			return self._body['created_by']
		return super()._raw_author

    ###########################################################################
    # Propiedades
    ###########################################################################

	@property
	def html(self):
		return self._pdata[2]

	@pyscp.utils.cached_property
	@pyscp.utils.listify()
	def history(self):
		"""Retorna el historial de revisiones de la página."""
		data = self._module(
			'history/PageRevisionListModule', page=1, perpage=99999)['body']
		soup = bs4.BeautifulSoup(data, 'lxml')
		for row in reversed(soup('tr')[1:]):
			rev_id = int(row['id'].split('-')[-1])
			cells = row('td')
			number = int(cells[0].text.strip('.'))
			user = cells[4].text
			time = parse_element_time(cells[5])
			comment = cells[6].text if cells[6].text else None
			yield pyscp.core.Revision(rev_id, number, user, time, comment)

	@pyscp.utils.cached_property
	def votes(self):
		"""Retorna todos los votos hechos en la página."""
		data = self._module('pagerate/WhoRatedPageModule')['body']
		soup = bs4.BeautifulSoup(data, 'lxml')
		spans = [i.text.strip() for i in soup('span')]
		pairs = zip(spans[::2], spans[1::2])
		return [pyscp.core.Vote(u, 1 if v == '+' else -1) for u, v in pairs]

	@property
	def tags(self):
		if 'tags' in self._body:
			return set(self._body['tags'].split())
		return self._pdata[3]

	@property
	def source(self):
		data = self._module('viewsource/ViewSourceModule')['body']
		soup = bs4.BeautifulSoup(data, 'lxml')
		return soup.text[11:].strip().replace(chr(160), ' ')

	@property
	def created(self):
		if 'created_at' in self._body:
			time = arrow.get(self._body['created_at'], 'DD MMM YYYY HH:mm')
			return time.format('YYYY-MM-DD HH:mm:ss')
		return super().created

	@property
	def rating(self):
		if 'rating' in self._body:
			return int(self._body['rating'])
		return super().rating

    ###########################################################################
    # Metodos Para Modificar Páginas
    ###########################################################################

	def edit(self, source, title=None, comment=None):
		"""Sobreescribe la página con un nuevo código y título."""
		if title is None:title = self._raw_title
		self._flush('html', 'history', 'source')
		wiki_page = self.url.split('/')[-1]
		lock = self._module(
			'edit/PageEditModule',
			mode='page',
			wiki_page=wiki_page,
			force_lock=True)
		return self._action(
			'savePage',
			source=source,
			title=title,
			comments=comment,
			wiki_page=wiki_page,
			lock_id=lock['lock_id'],
			lock_secret=lock['lock_secret'],
			revision_id=lock.get('page_revision_id', None))

	def create(self, source, title, comment=None):
		if not hasattr(self, '_cache'):
			self._cache = {}
		self._cache['_pdata'] = (None, None, None)
		response = self.edit(source, title, comment)
		del self._cache['_pdata']
		return response

	def revert(self, rev_n):
		"""Revierte la página a la revisión previa."""
		self._flush('html', 'history', 'source', 'tags')
		return self._action('revert', revisionId=self.history[rev_n].id)

	def set_tags(self, tags):
		"""Reemplaza las etiquetas en la página."""
		res = self._action('saveTags', tags=' '.join(tags))
		self._flush('history', '_pdata')
		return res

	def upload(self, name, data):
		url = self._wiki.site + '/default--flow/files__UploadTarget'
		kwargs = dict(
			pageId=self._id,
			page_id=self._id,
			action='FileAction',
			event='uploadFile',
			MAX_FILE_SIZE=52428800)
		response = self._wiki.req.post(
			url,
			data=kwargs,
			files={'userfile': (name, data)},
			cookies={'wikidot_token7': '123456'})
		response = bs4.BeautifulSoup(response.text, 'lxml')
		status = response.find(id='status').text
		message = response.find(id='message').text
		if status != 'ok':
			raise RuntimeError(message)
		return response						 
    ###########################################################################
    # Métodos de Votación
    ###########################################################################

	def upvote(self):
		self._vote(1)
		self._flush('votes')

	def downvote(self):
		self._vote(-1)
		self._flush('votes')

	def cancel_vote(self):
		self._vote(0)
		self._flush('votes')


class Thread(pyscp.core.Thread):

	@pyscp.utils.cached_property
	@pyscp.utils.listify()
	def posts(self):
		if self._id is None:
			return
		pages = self._wiki._pager(
			'forum/ForumViewThreadPostsModule', _key='pageNo', t=self._id)
		pages = (bs4.BeautifulSoup(p['body'], 'lxml').body for p in pages)
		pages = (p for p in pages if p)
		posts = (p(class_='post-container', recursive=False) for p in pages)
		posts = itertools.chain.from_iterable(posts)
		for post, parent in crawl_posts(posts):
			post_id = int(post['id'].split('-')[1])
			title = post.find(class_='title').text.strip()
			title = title if title else None
			content = post.find(class_='content')
			content.attrs.clear()
			content = str(content)
			user = post.find(class_='printuser').text
			time = parse_element_time(post)
			yield pyscp.core.Post(post_id, title, content, user, time, 
			parent)

	def new_post(self, source, title=None, parent_id=None):
		return self._wiki._module(
			'Empty',
			threadId=self._id,
			parentId=parent_id,
			title=title,
			source=source,
			action='ForumAction',
			event='savePost')


class Wiki(pyscp.core.Wiki):
	"""
	Crea un objeto Wiki.
	
	Esta clase no utiliza nada de la API oficial de Wikidot, en cambio
	confia en enviar la petición http post/get a la página interna de Wikidot y
	analizar los datos retornados.
	"""

	Page = Page
	Thread = Thread
	# Tautology = Tautology

    ###########################################################################
    # Métodos Especiales
    ###########################################################################

	def __init__(self, site):
		super().__init__(site)
		self.req = InsistentRequest()

	def __repr__(self):
		return '{}.{}({})'.format(
			self.__module__,
			self.__class__.__name__,
			repr(self.site))

    ###########################################################################
    # Métodos Internos
    ###########################################################################

	@pyscp.utils.log_errors(log.warning)
	def _module(self, _name, **kwargs):
		"""
		Llama a un modulo Wikidot.
		
		Este método es el responsable del funcionamiento de la mayoria de las clases.
		Casi todos los otros métodos de la clase estan usando _module de una
		u otra forma.
		"""
		return self.req.post(
			self.site + '/ajax-module-connector.php',
			data=dict(
				pageId=kwargs.get('page_id', None),  # puto wikidot
				moduleName=_name,
				# token7 puede ser cualquier número de 6 dígitos, con tal de que sea el mismo
				# en el payload y en el cookie
				wikidot_token7='123456',
				**kwargs),
			headers={'Content-Type': 'application/x-www-form-urlencoded;'},
			cookies={'wikidot_token7': '123456'}).json()

	def _pager(self, _name, _key, _update=None, **kwargs):
		"""Itera sobre los resultados del módulo multi-page."""
		first_page = self._module(_name, **kwargs)
		yield first_page
		counter = bs4.BeautifulSoup(
			first_page['body'], 'lxml').find(class_='pager-no')
		if not counter:
			return
		for idx in range(2, int(counter.text.split(' ')[-1]) + 1):
			kwargs.update({_key: idx if _update is None else _update(idx)})
			yield self._module(_name, **kwargs)

	def _list_pages_raw(self, **kwargs):
		"""
		Llama al modulo ListPages.
		
		Wikidot's ListPages is an extremely versatile php module that can be
		used to retrieve all sorts of interesting informations, from urls of
		pages created by a given user, and up to full html contents of every
		page on the site.
		"""
		yield from self._pager(
			'list/ListPagesModule',
			_key='offset',
			_update=lambda x: 250 * (x - 1),
			perPage=250,
			**kwargs)

	def _list_pages_parsed(self, **kwargs):
		"""
		Llama al modulo ListPages y analiza los resultados.
		
		Sets default arguments, parses ListPages body into a namedtuple.
		Returns Page instances with a _body grafted in.
		"""
		keys = set(kwargs.pop('body', '').split() + ['fullname'])
		kwargs['module_body'] = '\n'.join(
			map('||{0}||%%{0}%% ||'.format, keys))
		kwargs['created_by'] = kwargs.pop('author', None)
		lists = self._list_pages_raw(**kwargs)
		soups = (bs4.BeautifulSoup(p['body'], 'lxml') for p in lists)
		pages = (s.select('div.list-pages-item') for s in soups)
		pages = itertools.chain.from_iterable(pages)
		for page in pages:
			data = {
				r('td')[0].text: r('td')[1].text.strip() for r in page('tr')}
			page = self(data['fullname'])
			page._body = data
			yield page

    ###########################################################################
    # Métodos Públicos
    ###########################################################################

	def auth(self, username, password):
		"""Inicia sesión en wikidot dado el par username/password."""
		return self.req.post(
			'https://www.wikidot.com/default--flow/login__LoginPopupScreen',
			data=dict(
				login=username,
				password=password,
				action='Login2Action',
				event='login'))

	def list_categories(self):
		"""Retorna las categorias del foro."""
		data = self._module('forum/ForumStartModule')['body']
		soup = bs4.BeautifulSoup(data, 'lxml')
		for elem in [e.parent for e in soup(class_='name')]:
			cat_id = parse_element_id(elem.select('.title a')[0])
			title, description, size = [
				elem.find(class_=i).text.strip()
				for i in ('title', 'description', 'threads')]
			yield pyscp.core.Category(
				cat_id, title, description, int(size))

	def list_threads(self, category_id):
		"""Retorna los hilos en la categoria dada."""
		pages = self._pager(
			'forum/ForumViewCategoryModule', _key='p', c=category_id)
		soups = (bs4.BeautifulSoup(p['body'], 'lxml') for p in pages)
		elems = (s(class_='name') for s in soups)
		for elem in itertools.chain(*elems):
			thread_id = parse_element_id(elem.select('.title a')[0])
			title, description = [
				elem.find(class_=i).text.strip()
				for i in ('title', 'description')]
			yield self.Thread(self, thread_id, title, description)

	def send_pm(self, username, text, title=None):
		lookup = self.req.get(
			'https://www.wikidot.com/quickmodule.php?'
			'module=UserLookupQModule&q=' + username).json()
		if not lookup['users'] or lookup['users'][0]['name'] != username:
			raise ValueError('Usuario No Encontrado')
		user_id = lookup['users'][0]['user_id']
		return self.req.post(
			'https://www.wikidot.com/ajax-module-connector.php',
			data=dict(
				moduleName='Empty',
				source=text,
				subject=title,
				to_user_id=user_id,
				action='DashboardMessageAction',
				event='send',
				wikidot_token7='123456'),
			headers={'Content-Type': 'application/x-www-form-urlencoded;'},
			cookies={'wikidot_token7': '123456'}).json()

    ###########################################################################
    # Métodos Específicos Para La Wiki SCP
    ###########################################################################

	@functools.lru_cache(maxsize=1)
	@pyscp.utils.listify()
	def list_images(self):
		if 'lafundacionscp' not in self.site:
			return
		base = 'http://borradores-scp-es.wikidot.com/image-review-{}'
		urls = [base.format(i) for i in range(1, 36)]
		pages = [self.req.get(u).text for u in urls]
		soups = [bs4.BeautifulSoup(p, 'lxml') for p in pages]
		elems = [s('tr') for s in soups]
		elems = itertools.chain(*elems)
		elems = [e('td') for e in elems]
		elems = [e for e in elems if e]
		for elem in elems:
			url = elem[0].find('img')['src']
			source = elem[2].a['href'] if elem[2]('a') else None
			status, notes = [elem[i].text for i in (3, 4)]
			status, notes = [i if i else None for i in (status, notes)]
			yield pyscp.core.Image(url, source, status, notes, None)

###############################################################################


@pyscp.utils.ignore((IndexError, TypeError))
def parse_element_id(element):
	"""Extrae el número id del enlace."""
	return int(element['href'].split('/')[2].split('-')[1])


def parse_element_time(element):
	"""Extrae y formatea la hora por un elemento HTML."""
	unixtime = element.find(class_='odate')['class'][1].split('_')[1]
	return arrow.get(unixtime).format('YYYY-MM-DD HH:mm:ss')


def crawl_posts(post_containers, parent=None):
	"""
	Repure los post del árbol de comentarios.
	
	Por cada contenedor de post en la lista dada, retorna una tupla de
	(post, padre). Then recurses onto all the post-container children
	of the current post-container.
	"""
	for container in post_containers:
		yield container.find(class_='post'), parent
		yield from crawl_posts(
			container(class_='post-container', recursive=False),
			int(container['id'].split('-')[1]))
