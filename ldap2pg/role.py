from __future__ import unicode_literals

from collections import OrderedDict
import logging

from .psql import Query
from .utils import collect_fields, dedent, unicode, FormatList


logger = logging.getLogger(__name__)


class Role(object):
    __slots__ = (
        'comment',
        'lname',
        'members',
        'name',
        'options',
        'parents',
    )

    def __init__(self, name, options=None, members=None, parents=None,
                 comment=None):
        self.name = name
        self.lname = name.lower()
        self.members = members or []
        self.options = RoleOptions(options or {})
        self.parents = parents or []
        self.comment = comment

    def __eq__(self, other):
        return self.name == unicode(other)

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return '<%s %s>' % (self.__class__.__name__, self.name)

    def __str__(self):
        return self.name

    def __lt__(self, other):
        return unicode(self) < unicode(other)

    @classmethod
    def from_row(cls, name, members=None, *row):
        self = Role(name=name, members=list(filter(None, members or [])))
        self.options.update_from_row(row)
        self.options.fill_with_defaults()
        return self

    def create(self):
        yield Query(
            'Create %s.' % (self.name,),
            None,
            dedent("""\
            CREATE ROLE "{role}" WITH {options};
            COMMENT ON ROLE "{role}" IS '{comment}';
            """).format(
                role=self.name, options=self.options,
                comment=self.comment or 'Managed by ldap2pg.')
        )
        if self.members:
            yield Query(
                'Add %s members.' % (self.name,),
                None,
                'GRANT "%(role)s" TO %(members)s;' % dict(
                    members=", ".join(map(lambda x: '"%s"' % x, self.members)),
                    role=self.name,
                ),
            )

    def alter(self, other):
        # Yields SQL queries to reach other state.

        if self.options != other.options:
            yield Query(
                'Update options of %s.' % (self.name,),
                None,
                dedent("""\
                ALTER ROLE "{role}" WITH {options};
                COMMENT ON ROLE "{role}" IS 'Managed by ldap2pg.';
                """).format(role=self.name, options=other.options)
            )

        if self.members != other.members:
            missing = set(other.members) - set(self.members)
            if missing:
                logger.debug(
                    "Role %s miss members %s.",
                    self.name, ', '.join(missing)
                )
                yield Query(
                    'Add missing %s members.' % (self.name,),
                    None,
                    "GRANT \"%(role)s\" TO %(members)s;" % dict(
                        members=", ".join(map(lambda x: '"%s"' % x, missing)),
                        role=self.name,
                    ),
                )
            spurious = set(self.members) - set(other.members)
            if spurious:
                yield Query(
                    'Delete spurious %s members.' % (self.name,),
                    None,
                    "REVOKE \"%(role)s\" FROM %(members)s;" % dict(
                        members=", ".join(map(lambda x: '"%s"' % x, spurious)),
                        role=self.name,
                    ),
                )

    _drop_objects_sql = dedent("""
    DO $$BEGIN EXECUTE 'GRANT "%(role)s" TO '||SESSION_USER; END$$;
    DO $$BEGIN EXECUTE 'REASSIGN OWNED BY "%(role)s" TO '||SESSION_USER; END$$;
    DROP OWNED BY "%(role)s";
    """)

    def drop(self):
        yield Query(
            'Reassign %s objects and purge ACL on %%(dbname)s.' % (self.name,),
            Query.ALL_DATABASES,
            self._drop_objects_sql % dict(role=self.name),
        )
        yield Query(
            'Drop %s.' % (self.name,),
            None,
            "DROP ROLE \"%(role)s\";" % dict(role=self.name),
        )

    def merge(self, other):
        self.options.update(other.options)
        self.members += other.members
        self.parents += other.parents
        return self

    def rename(self):
        yield Query(
            'Rename %s to %s.' % (self.lname, self.name),
            None,
            """ALTER ROLE "%(old)s" RENAME TO "%(new)s" ;""" % dict(
                old=self.lname, new=self.name,
            ),
        )

    def rename_members(self, renamed):
        # Replace old names in members by matching new one.
        renamed_members = [
            renamed[m] for m in self.members if m in renamed]
        for m in renamed_members:
            self.members.remove(m.lname)
            self.members.append(m.name)


class RoleOptions(dict):
    COLUMNS = OrderedDict([
        # column: (option, default)
        ('rolbypassrls', ('BYPASSRLS', False)),
        ('rolcanlogin', ('LOGIN', False)),
        ('rolcreatedb', ('CREATEDB', False)),
        ('rolcreaterole', ('CREATEROLE', False)),
        ('rolinherit', ('INHERIT', True)),
        ('rolreplication', ('REPLICATION', False)),
        ('rolsuper', ('SUPERUSER', False)),
    ])

    SUPERONLY_COLUMNS = ['rolsuper', 'rolreplication', 'rolbypassrls']
    SUPPORTED_COLUMNS = list(COLUMNS.keys())

    @classmethod
    def supported_options(cls):
        return [
            o for c, (o, _) in cls.COLUMNS.items()
            if c in cls.SUPPORTED_COLUMNS
        ]

    COLUMNS_QUERY = dedent("""
    SELECT array_agg(attrs.attname)
    FROM pg_catalog.pg_namespace AS nsp
    JOIN pg_catalog.pg_class AS tables
      ON tables.relnamespace = nsp.oid AND tables.relname = 'pg_authid'
    JOIN pg_catalog.pg_attribute AS attrs
      ON attrs.attrelid = tables.oid AND attrs.attname LIKE 'rol%'
    WHERE nsp.nspname = 'pg_catalog'
    ORDER BY 1
    """)

    @classmethod
    def update_supported_columns(cls, columns):
        cls.SUPPORTED_COLUMNS = [
            c for c in cls.SUPPORTED_COLUMNS
            if c in columns
        ]
        logger.debug(
            "Postgres server supports role options %s.",
            ", ".join(cls.supported_options()),
        )

    @classmethod
    def filter_super_columns(cls):
        cls.SUPPORTED_COLUMNS = [
            c for c in cls.SUPPORTED_COLUMNS
            if c not in cls.SUPERONLY_COLUMNS
        ]

    def __init__(self, *a, **kw):
        defaults = dict([(o, None) for c, (o, d) in self.COLUMNS.items()])
        super(RoleOptions, self).__init__(**defaults)
        init = dict(*a, **kw)
        self.update(init)

    def __repr__(self):
        return '<%s %s>' % (self.__class__.__name__, self)

    def __str__(self):
        return ' '.join((
            ('NO' if value is False else '') + name
            for name, value in self.items()
            if name in self.supported_options()
        ))

    def update_from_row(self, row):
        self.update(dict(zip(self.supported_options(), row)))

    def update(self, other):
        spurious_options = set(other.keys()) - set(self.keys())
        if spurious_options:
            message = "Unknown options %s" % (', '.join(spurious_options),)
            raise ValueError(message)

        for k, their in other.items():
            my = self[k]
            if their is None:
                continue
            if my is None:
                self[k] = their
            elif my != their:
                raise ValueError("Two values defined for option %s." % k)

    def fill_with_defaults(self):
        defaults = dict([(o, d) for c, (o, d) in self.COLUMNS.items()])
        for k, v in self.items():
            if v is None:
                self[k] = defaults[k]


class RoleSet(set):
    def resolve_membership(self):
        index_ = self.reindex()
        for role in self:
            while role.parents:
                parent_name = role.parents.pop()
                try:
                    parent = index_[parent_name]
                except KeyError:
                    raise ValueError('Unknown parent role %s' % parent_name)
                if role.name in parent.members:
                    continue
                parent.members.append(role.name)

    def reindex(self):
        return dict([(role.name, role) for role in self])

    def flatten(self):
        # Generates the flatten tree of roles, children first.

        index = self.reindex()
        seen = set()

        def walk(name):
            if name in seen:
                return
            try:
                role = index[name]
            except KeyError:
                # We are trying to walk a member out of set. This is the case
                # where a role is missing but not one of its member.
                return

            for member in role.members:
                for i in walk(member):
                    yield i
            yield name
            seen.add(name)

        for name in sorted(index.keys()):
            for i in walk(name):
                yield index[i]

    def union(self, other):
        return self.__class__(self | other)

    def diff(self, other=None, available=None):
        # Yield query so that self match other. It's kind of a three-way diff
        # since we reuse `available` roles instead of recreating roles.

        available = available or RoleSet()
        other = other or RoleSet()

        # First create/rename missing roles
        missing = RoleSet(other - available)
        renamed = dict()
        for role in missing.flatten():
            # Detect renames from lowercase.
            if role.lname in self and role.lname not in other:
                logger.debug(
                    "Detected rename from %s to %s.", role.lname, role.name)
                for qry in role.rename():
                    yield qry
                missing.remove(role)
                renamed[role.lname] = role

        for role in missing.flatten():
            for qry in role.create():
                yield qry

        # Now update existing roles options and memberships
        existing = available & other
        my_roles_index = available.reindex()
        other_roles_index = other.reindex()
        for role in existing:
            mine = my_roles_index[role.name]
            mine.rename_members(renamed)
            its = other_roles_index[role.name]
            if role not in self:
                logger.warning(
                    "Role %s already exists in cluster. Reusing.", role.name)
            for qry in mine.alter(its):
                yield qry

        # Don't forget to trash all spurious managed roles!
        spurious = RoleSet(self - other - set(renamed) - set(['public']))
        for role in reversed(list(spurious.flatten())):
            for qry in role.drop():
                yield qry


class RoleRule(object):
    def __init__(self, names, parents=None, members=None, options=None,
                 comment=None):
        self.comment = FormatList.factory([comment] if comment else [])
        self.members = FormatList.factory(members or [])
        self.names = FormatList.factory(names or [])
        self.options = options or {}
        self.parents = FormatList.factory(parents or [])
        self.all_fields = collect_fields(
            self.comment, self.members, self.names, self.parents,
        )

    def __repr__(self):
        return '<%s %s>' % (self.__class__.__name__, self.names)

    def build_comment(self, vars_):
        if not self.comment:
            return

        comments = self.comment.expand(vars_)
        try:
            comment = next(comments)
        except StopIteration:
            logger.warning(
                "Can't generate comment for %s... Missing attribute?",
                vars_['dn'][0][:24])
            return

        try:
            next(comments)
            logger.warning(
                "Multiple comments are generated for %s.",
                vars_['dn'][0][:24],
            )
        except StopIteration:
            pass
        return comment

    def generate(self, vars_):
        members = list(self.members.expand(vars_))
        parents = list(self.parents.expand(vars_))
        comment = self.build_comment(vars_)

        for name in self.names.expand(vars_):
            yield Role(
                name=name,
                members=members[:],
                options=self.options,
                parents=parents[:],
                comment=comment,
            )

    def as_dict(self):
        dict_ = {
            'comment': self.comment.formats[0] if self.comment else None,
            'options': self.options,
        }
        for k in "names", "members", "parents":
            dict_[k] = getattr(self, k).formats
        return dict_
