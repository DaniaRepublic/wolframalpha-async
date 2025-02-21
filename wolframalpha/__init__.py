import itertools
import json
import urllib.parse
import urllib.request
import aiohttp
import contextlib
import collections
from typing import Dict, Any, Callable, Tuple

import xmltodict
from more_itertools import always_iterable


def xml_bool(str_val):
    """
    >>> xml_bool('true')
    True
    >>> xml_bool('false')
    False
    """
    return bool(json.loads(str_val))


class Client:
    """
    Wolfram|Alpha v2.0 client
    """

    def __init__(self, app_id):
        self.app_id = app_id

    async def query(self, input, params=(), **kwargs):
        """
        Query Wolfram|Alpha using the v2.0 API

        This function is now asynchronous.
        """
        data = dict(
            input=input,
            appid=self.app_id,
        )
        data = itertools.chain(params, data.items(), kwargs.items())

        query = urllib.parse.urlencode(tuple(data))
        url = 'https://api.wolframalpha.com/v2/query?' + query

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                assert resp.content_type == 'text/xml'
                assert resp.charset == 'utf-8'
                text = await resp.text()
                doc = xmltodict.parse(text, postprocessor=Document.make)
        return doc['queryresult']

# Example usage:
# 
# asyncio.run(client.query('temperature in Washington, DC on October 3, 2012'))


class ErrorHandler:
    def __init__(self, *args, **kwargs):
        super(ErrorHandler, self).__init__(*args, **kwargs)
        self._handle_error()

    def _handle_error(self):
        if 'error' not in self:
            return

        template = 'Error {error[code]}: {error[msg]}'
        raise Exception(template.format(**self))


def identity(x):
    return x


class Document(dict):
    _attr_types: Dict[str, Callable[[str], Any]] = collections.defaultdict(
        lambda: identity,
        height=int,
        width=int,
        numsubpods=int,
        position=float,
        primary=xml_bool,
        success=xml_bool,
    )
    children: Tuple[str, ...] = ()

    @classmethod
    def _find_cls(cls, key):
        """
        Find a possible class for wrapping an item by key.
        """
        matching = (
            sub
            for sub in cls.__subclasses__()
            if key == getattr(sub, 'key', sub.__name__.lower())
        )
        return next(matching, identity)

    @classmethod
    def make(cls, path, key, value):
        value = cls._find_cls(key)(value)
        value = cls._attr_types[key.lstrip('@')](value)
        return key, value

    def __getattr__(self, name):
        return self._get_children(name) or self._get_attr(name)

    def _get_attr(self, name):
        attr_name = '@' + name
        try:
            val = self[name] if name in self else self[attr_name]
        except KeyError:
            raise AttributeError(name)
        return val

    def _get_children(self, name):
        if name not in self.__class__.children:
            return
        singular = name.rstrip('s')
        try:
            val = self._get_attr(singular)
        except AttributeError:
            val = None
        return always_iterable(val, base_type=dict)


class Assumption(Document):
    @property
    def text(self):
        text = self.template.replace('${desc1}', self.description)
        with contextlib.suppress(Exception):
            text = text.replace('${word}', self.word)
        return text[: text.index('. ') + 1]


class Warning(Document):
    pass


class Image(Document):
    """
    Holds information about an image included with an answer.
    """

    key = 'img'


class Subpod(Document):
    """
    Holds a specific answer or additional information relevant to said answer.
    """


class Pod(ErrorHandler, Document):
    """
    Groups answers and information contextualizing those answers.
    """

    children = ('subpods',)

    @property
    def primary(self):
        return self.get('@primary', False)

    @property
    def texts(self):
        """
        The text from each subpod in this pod as a list.
        """
        return [subpod.plaintext for subpod in self.subpods]

    @property
    def text(self):
        return next(iter(self.subpods)).plaintext


class Result(ErrorHandler, Document):
    """
    Handles processing the response for the programmer.
    """

    key = 'queryresult'
    children = 'pods', 'assumptions', 'warnings'

    @property
    def info(self):
        """
        The pods, assumptions, and warnings of this result.
        """
        return itertools.chain(self.pods, self.assumptions, self.warnings)

    def __iter__(self):
        return self.info

    def __len__(self):
        return sum(1 for _ in self)

    def __bool__(self):
        return bool(len(self))

    @property
    def results(self):
        """
        The pods that hold the response to a simple, discrete query.
        """
        return (pod for pod in self.pods if pod.primary or pod.title == 'Result')

    @property
    def details(self):
        """
        A simplified set of answer text by title.
        """
        return {pod.title: pod.text for pod in self.pods}

