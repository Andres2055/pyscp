#!/usr/bin/env python3

"""
Actualizador de Estadísticas.

Calcula las estadísticas de una wiki y las escribe en otra.
"""

###############################################################################
# Módulos Importados
###############################################################################

import logging

from pyscp import snapshot, wikidot, utils
from pyscp.stats import scalars, counters, filters

###############################################################################
# Constantes Globales Y Variables
###############################################################################

log = logging.getLogger(__name__)

###############################################################################


class Updater:

    scalars_author = (
        ('Páginas Creadas', len),
        ('Puntaje Neto', scalars.rating),
        ('Puntaje Medio', scalars.rating_average),
        ('Recuento de Palabras', scalars.wordcount),
        ('Recuento de Palabras Medio', scalars.wordcount_average))

    def __init__(self, source, target):
        self.pages = list(source.list_pages())
        self.target = target
        self.exist = [p.url for p in target.list_pages()]

    @staticmethod
    def source_counter(counter):
        """Construye un código de marcado wikidot para el ranking de las páginas."""
        source = ['||~ Posición||~ Usuario||~ Puntaje||']
        # ordenado por puntaje, después alfabeticamente por usuarios
        items = sorted(counter.items(), key=lambda x: x[0].lower())
        items = sorted(items, key=lambda x: x[1], reverse=True)
        template = '||{}||[[[user:{}]]]||{}||'
        for idx, (user, score) in enumerate(items):
            source.append(template.format(idx + 1, user, score))
        return '\n'.join(source)

    def source_author(self, user):
        """Construye un código fuente para las estadísticas de autoridad de los usuarios."""
        pages = filters.user(self.pages, user)
        source = ['++ Estadísticas de Autoridad']
        if not pages:
            source.append('Este usuario no posee autoria de ninguna página.')
            return '\n'.join(source)
        for descr, func in self.scalars_author:
            text = '[[[posición:{}]]]:@@{}@@**{}**'.format(
                descr, ' ' * (40 - len(descr)), round(func(pages), 2))
            source.append('{{%s}}' % text)
        return '\n'.join(source)

    def post(self, name, source):
        """Actualiza, si existe; crea, si no; reintenta si falla."""
        p = self.target(name)
        for _ in range(10):  # reintenta un máximo de diez veces
            if p.url in self.exist:
                response = p.edit(source)
            else:
                title = name.split(':')
                response = p.create(source, title)
            if response['status'] == 'ok':
                return
        log.error('Falla al postear: %s', name)

    def update_users(self):
        """Actualiza las estadísticas de la wiki con las estadísticas de los autores."""
        users = {p.author for p in self.pages}
        for user in utils.pbar(users, 'ACTUALIZANDO ESTADISTICAS DE USUARIOS'):
            self.post('usuario:' + user, self.source_author(user))

    def update_rankings(self):
        for descr, func in utils.pbar(
                self.scalars_author, 'ACTUALIZANDO POSICIÓN'):
            value = self.source_counter(counters.author(self.pages, func))
            self.post('posición:' + descr, round(value, 2))


###############################################################################

if __name__ == "__main__":
    source = snapshot.Wiki(
        'www.lafundacionscp.wikidot.com', '/home/anqxyr/heap/_scp/scp-wiki.2015-06-23.db')
    target = wikidot.Wiki('scp-es-stats')
    target.auth('andres2055', 'morrocoy')
    up = Updater(source, target)
    up.update_rankings()
    up.update_users()
