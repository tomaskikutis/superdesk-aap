# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2013, 2014 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license

import logging
import re
from superdesk.resource import Resource
from superdesk.services import BaseService
from superdesk.utils import ListCursor
from superdesk import get_resource_service
from superdesk.errors import SuperdeskApiError
from superdesk.io.subjectcodes import get_subjectcodeitems

logger = logging.getLogger(__name__)


class FilterConditionResource(Resource):
    schema = {
        'name': {
            'type': 'string',
            'nullable': False,
            'iunique': True
        },
        'field': {
            'type': 'string',
            'nullable': False,
            'allowed': ['anpa-category',
                        'urgency',
                        'keywords',
                        'priority',
                        'slugline',
                        'type',
                        'source',
                        'headline',
                        'body_html',
                        'genre',
                        'subject'],
        },
        'operator': {
            'type': 'string',
            'allowed': ['in',
                        'nin',
                        'like',
                        'notlike',
                        'startswith',
                        'endswith'],
            'nullable': False,
        },
        'value': {
            'type': 'string',
            'nullable': False,
        }
    }

    additional_lookup = {
        'url': 'regex("[\w,.:-]+")',
        'field': 'name'
    }

    datasource = {'default_sort': [('_created', -1)]}
    privileges = {'POST': 'publish_filters',
                  'PATCH': 'publish_filters',
                  'DELETE': 'publish_filters'}


class FilterConditionService(BaseService):
    def on_create(self, docs):
        self._check_equals(docs)
        self._check_parameters(docs)

    def on_update(self, updates, original):
        doc = dict(original)
        doc.update(updates)
        self._check_equals([doc])
        self._check_parameters([doc])

    def _check_parameters(self, docs):
        parameters = get_resource_service('filter_condition_parameters').get(req=None, lookup=None)
        for doc in docs:
            parameter = [p for p in parameters if p['field'] == doc['field']]
            if not parameter or len(parameter) == 0:
                raise SuperdeskApiError.badRequestError(
                    'Filter condition:{} has unidentified field: {}'
                    .format(doc['name'], doc['field']))
            if doc['operator'] not in parameter[0]['operators']:
                raise SuperdeskApiError.badRequestError(
                    'Filter condition:{} has unidentified operator: {}'
                    .format(doc['name'], doc['operator']))

    def _check_equals(self, docs):
        for doc in docs:
            existing_docs = self.get(None, {'field': doc['field'], 'operator': doc['operator']})
            for existing_doc in existing_docs:
                if '_id' in doc and doc['_id'] == existing_doc['_id']:
                    continue
                if self._are_equal(doc, existing_doc):
                    raise SuperdeskApiError.badRequestError(
                        'Filter condition:{} has identical settings'.format(existing_doc['name']))

    def _are_equal(self, fc1, fc2):
        def get_comparer(fc):
            return ''.join(sorted(fc['value'].upper()))

        return all([fc1['field'] == fc2['field'],
                    fc1['operator'] == fc2['operator'],
                    get_comparer(fc1) == get_comparer(fc2)])

    def get_mongo_query(self, doc):
        field = self._get_field(doc['field'])
        operator = self._get_mongo_operator(doc['operator'])
        value = self._get_mongo_value(doc['operator'], doc['value'])
        return {field: {operator: value}}

    def _get_mongo_operator(self, operator):
        if operator in ['like', 'startswith', 'endswith']:
            return '$regex'
        elif operator == 'notlike':
            return '$not'
        else:
            return '${}'.format(operator)

    def _get_mongo_value(self, operator, value):
        if operator == 'startswith':
            return re.compile('^{}'.format(value), re.IGNORECASE)
        elif operator == 'like' or operator == 'notlike':
            return re.compile('.*{}.*'.format(value), re.IGNORECASE)
        elif operator == 'endswith':
            return re.compile('.*{}'.format(value), re.IGNORECASE)
        else:
            if isinstance(value, str) and value.find(',') > 0:
                if value.split(',')[0].strip().isdigit():
                    return [int(x) for x in value.split(',') if x.strip().isdigit()]
                else:
                    return value.split(',')
            else:
                return [value]

    def get_elastic_query(self, doc):
        operator = self._get_elastic_operator(doc['operator'])
        value = self._get_elastic_value(doc, doc['operator'], doc['value'])
        field = self._get_field(doc['field'])
        return {operator: {field: value}}

    def _get_elastic_operator(self, operator):
        if operator in ['in', 'nin']:
            return 'terms'
        else:
            return 'query_string'

    def _get_elastic_value(self, doc, operator, value):
        if operator in ['in', 'nin']:
            if isinstance(value, str) and value.find(',') > 0:
                if value.split(',')[0].strip().isdigit():
                    return [int(x) for x in value.split(',') if x.strip().isdigit()]
                else:
                    value.split(',')
            else:
                return [value]
        elif operator in ['like', 'notlike']:
            value = '{}:*{}*'.format(doc['field'], value)
            doc['field'] = 'query'
        elif operator == 'startswith':
            value = '{}:{}*'.format(doc['field'], value)
            doc['field'] = 'query'
        elif operator == 'endswith':
            value = '{}:*{}'.format(doc['field'], value)
            doc['field'] = 'query'
        return value

    def _get_field(self, field):
        if field == 'anpa-category':
            return 'anpa-category.value'
        elif field == 'genre':
            return 'genre.name'
        elif field == 'subject':
            return 'subject.qcode'
        else:
            return field

    def does_match(self, filter_condition, article):
        field = filter_condition['field']
        operator = filter_condition['operator']
        filter_value = filter_condition['value']

        if field not in article:
            if operator in ['nin', 'notlike']:
                return True
            else:
                return False

        article_value = self._get_field_value(field, article)
        filter_value = self._get_mongo_value(operator, filter_value)
        return self._run_filter(article_value, operator, filter_value)

    def _get_field_value(self, field, article):
        if field == 'anpa-category':
            return article[field]['value']
        elif field == 'genre':
            return [g['name'] for g in article[field]]
        elif field == 'subject':
            return [s['qcode'] for s in article[field]]
        else:
            return article[field]

    def _run_filter(self, article_value, operator, filter_value):
        if operator == 'in':
            if isinstance(article_value, list):
                return any([v in filter_value for v in article_value])
            else:
                return article_value in filter_value
        if operator == 'nin':
            if isinstance(article_value, list):
                return all([v not in filter_value for v in article_value])
            else:
                return article_value not in filter_value
        if operator == 'like' or operator == 'startswith' or operator == 'endswith':
            return filter_value.match(article_value)
        if operator == 'notlike':
            return not filter_value.match(article_value)


class FilterConditionParametersResource(Resource):
    url = "filter_conditions/parameters"
    resource_methods = ['GET']
    item_methods = []


class FilterConditionParametersService(BaseService):
    def get(self, req, lookup):
        values = self._get_field_values()
        return ListCursor([{'field': 'anpa-category',
                            'operators': ['in', 'nin'],
                            'values': values['anpa_category'],
                            'value_field': 'qcode'
                            },
                           {'field': 'urgency',
                            'operators': ['in', 'nin'],
                            'values': values['urgency'],
                            'value_field': 'value'
                            },
                           {'field': 'genre',
                            'operators': ['in', 'nin'],
                            'values': values['genre'],
                            'value_field': 'value'
                            },
                           {'field': 'subject',
                            'operators': ['in', 'nin'],
                            'values': values['subject'],
                            'value_field': 'qcode'
                            },
                           {'field': 'priority',
                            'operators': ['in', 'nin'],
                            'values': values['priority'],
                            'value_field': 'qcode'
                            },
                           {'field': 'keywords',
                            'operators': ['in', 'nin', 'like', 'notlike', 'startswith', 'endswith']
                            },
                           {'field': 'slugline',
                            'operators': ['in', 'nin', 'like', 'notlike', 'startswith', 'endswith']
                            },
                           {'field': 'type',
                            'operators': ['in', 'nin'],
                            'values': values['type'],
                            'value_field': 'value'
                            },
                           {'field': 'source',
                            'operators': ['in', 'nin', 'like', 'notlike', 'startswith', 'endswith']
                            },
                           {'field': 'headline',
                            'operators': ['in', 'nin', 'like', 'notlike', 'startswith', 'endswith']
                            },
                           {'field': 'body_html',
                            'operators': ['in', 'nin', 'like', 'notlike', 'startswith', 'endswith']
                            }])

    def _get_field_values(self):
        values = {}
        values['anpa_category'] = get_resource_service('vocabularies').find_one(req=None, _id='categories')['items']
        values['genre'] = get_resource_service('vocabularies').find_one(req=None, _id='genre')['items']
        values['urgency'] = get_resource_service('vocabularies').find_one(req=None, _id='newsvalue')['items']
        values['priority'] = get_resource_service('vocabularies').find_one(req=None, _id='priority')['items']
        values['type'] = get_resource_service('vocabularies').find_one(req=None, _id='type')['items']
        values['subject'] = get_subjectcodeitems()
        return values