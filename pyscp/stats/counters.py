#!/usr/bin/env python3

"""
Counters.

Take a list of pages and a scalar, and return a collections.Counter instance.
"""

###############################################################################
# Importaciones
###############################################################################

import collections
import re

###############################################################################


def make_counter(pages, func, key):
    """Constructor de contador genérico."""
    subgroups = collections.defaultdict(list)
    for p in pages:
        key_value = key(p)
        if key_value:
            subgroups[key_value].append(p)
    return collections.Counter({k: func(v) for k, v in subgroups.items()})


def author(pages, func):
    """Agrupa por página de autor."""
    return make_counter(pages, func, lambda p: p.author)


def month(pages, func):
    """Agrupa por el mes en que la página fue posteado."""
    return make_counter(pages, func, lambda p: p.created[:7])


def page(pages, func):
    """Cada página en su propio grupo."""
    return make_counter(pages, func, lambda p: p.url)


def block(pages, func):
    """Group skips based on which 100-block they're in."""
    def key(page):
        if 'scp' not in page.tags:
            return
        match = re.search(r'[0-9]{3,4}$', page.url)
        if not match:
            return
        match = int(match.group())
        if match == 1:
            return
        return str((match // 100) * 100).zfill(3)
    return make_counter(pages, func, key)


def chain(pages, func, *counters):
    """Aplicar contadores uno detras de otro."""
    if len(counters) == 1:
        return counters[0](pages, func)
    results = collections.Counter()
    for key, val in counters[0](pages, lambda x: x).items():
        for ikey, ival in chain(val, func, *counters[1:]).items():
            results['%s, %s' % (key, ikey)] = ival
    return results
