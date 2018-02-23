from __future__ import unicode_literals

import logging
import re

import psycopg2.extensions

from .utils import AllDatabases, UserError, urlparse, urlunparse


logger = logging.getLogger(__name__)


psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)


_dbname_re = re.compile("dbname *= *'?[^ ]*'?")


def inject_database_in_connstring(connstring, dbname):
    if dbname is None:
        return connstring

    if connstring.startswith('postgres://'):
        pr = list(urlparse(connstring))
        pr[2] = dbname
        return urlunparse(pr)
    else:
        connstring = _dbname_re.sub('', connstring)
        return connstring + " dbname=%s" % (dbname,)


class PSQL(object):
    # A simple connexion manager to Postgres
    #
    # For now, ldap2pg self limits it's connexion pool to 256 sessions. Later
    # if we hit the limit, we'll see how to managed this better.
    def __init__(self, connstring=None, max_pool_size=256):
        self.connstring = connstring or ''
        self.pool = {}
        self.max_pool_size = max_pool_size

    def __call__(self, dbname=None):
        if dbname in self.pool:
            session = self.pool[dbname]
        elif len(self.pool) >= self.max_pool_size:
            msg = (
                "Database limit exceeded.\n"
                "ldap2pg doesn't support cluster with more than %d databases."
            ) % (self.max_pool_size)
            raise UserError(msg)
        else:
            connstring = inject_database_in_connstring(self.connstring, dbname)
            self.pool[dbname] = session = PSQLSession(connstring.strip())

        return session

    def itersessions(self, databases):
        # Generate a session for each database. Handful for iterating queries
        # in each databases in the cluster.
        for dbname in databases:
            with self(dbname) as session:
                yield dbname, session


class PSQLSession(object):
    def __init__(self, connstring):
        self.connstring = connstring
        self.conn = None
        self.cursor = None

    def __del__(self):
        if self.cursor:
            self.cursor.close()
            self.cursor = None
        if self.conn:
            logger.debug(
                "Closing Postgres connexion to '%s'.", self.connstring)
            self.conn.close()
            self.conn = None

    def __enter__(self):
        if self.conn:
            logger.debug("Using Postgres connection to '%s'.", self.connstring)
        else:
            logger.debug("Connecting to Postgres '%s'.", self.connstring)
            self.conn = psycopg2.connect(self.connstring)
        if not self.cursor:
            self.cursor = self.conn.cursor()
        return self

    def __exit__(self, *a):
        pass

    def __call__(self, query, *args):
        logger.debug("Doing:\n%s", query.strip())
        self.cursor.execute(query, *args)
        self.conn.commit()
        return self.cursor

    def mogrify(self, qry, *a, **kw):
        qry = qry.encode('utf-8')
        sql = self.cursor.mogrify(qry, *a, **kw)
        return sql.decode(self.conn.encoding)


class Query(object):
    ALL_DATABASES = AllDatabases()

    def __init__(self, message, dbname, *args):
        self.message = message
        self.dbname = dbname
        self.args = args

    def __repr__(self):
        return "<%s on %s: %r>" % (
            self.__class__.__name__,
            self.dbname,
            self.args[0][:50] + '...',
        )

    def __str__(self):
        return self.message

    def expand(self, databases):
        if self.dbname is self.ALL_DATABASES:
            for dbname in databases:
                yield Query(
                    self.message % dict(dbname=dbname),
                    dbname,
                    *self.args
                )
        else:
            yield self


def expandqueries(queries, databases):
    for query in queries:
        for single_query in query.expand(databases):
            yield single_query
