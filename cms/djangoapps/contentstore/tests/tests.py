import json
from django.test import TestCase
from django.test.client import Client
from mock import patch, Mock
from override_settings import override_settings
from django.conf import settings
from django.core.urlresolvers import reverse
from path import path

from student.models import Registration
from django.contrib.auth.models import User
from xmodule.modulestore.django import modulestore
import xmodule.modulestore.django
from xmodule.modulestore import Location
from xmodule.modulestore.xml_importer import import_from_xml
import copy


def parse_json(response):
    """Parse response, which is assumed to be json"""
    return json.loads(response.content)


def user(email):
    '''look up a user by email'''
    return User.objects.get(email=email)


def registration(email):
    '''look up registration object by email'''
    return Registration.objects.get(user__email=email)


class ContentStoreTestCase(TestCase):
    def _login(self, email, pw):
        '''Login.  View should always return 200.  The success/fail is in the
        returned json'''
        resp = self.client.post(reverse('login_post'),
                                {'email': email, 'password': pw})
        self.assertEqual(resp.status_code, 200)
        return resp

    def login(self, email, pw):
        '''Login, check that it worked.'''
        resp = self._login(email, pw)
        data = parse_json(resp)
        self.assertTrue(data['success'])
        return resp

    def _create_account(self, username, email, pw):
        '''Try to create an account.  No error checking'''
        resp = self.client.post('/create_account', {
            'username': username,
            'email': email,
            'password': pw,
            'location': 'home',
            'language': 'Franglish',
            'name': 'Fred Weasley',
            'terms_of_service': 'true',
            'honor_code': 'true',
        })
        return resp

    def create_account(self, username, email, pw):
        '''Create the account and check that it worked'''
        resp = self._create_account(username, email, pw)
        self.assertEqual(resp.status_code, 200)
        data = parse_json(resp)
        self.assertEqual(data['success'], True)

        # Check both that the user is created, and inactive
        self.assertFalse(user(email).is_active)

        return resp

    def _activate_user(self, email):
        '''Look up the activation key for the user, then hit the activate view.
        No error checking'''
        activation_key = registration(email).activation_key

        # and now we try to activate
        resp = self.client.get(reverse('activate', kwargs={'key': activation_key}))
        return resp

    def activate_user(self, email):
        resp = self._activate_user(email)
        self.assertEqual(resp.status_code, 200)
        # Now make sure that the user is now actually activated
        self.assertTrue(user(email).is_active)


class AuthTestCase(ContentStoreTestCase):
    """Check that various permissions-related things work"""

    def setUp(self):
        self.email = 'a@b.com'
        self.pw = 'xyz'
        self.username = 'testuser'
        self.client = Client()

    def check_page_get(self, url, expected):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, expected)
        return resp

    def test_public_pages_load(self):
        """Make sure pages that don't require login load without error."""
        pages = (
                 reverse('login'),
                 reverse('signup'),
                 )
        for page in pages:
            print "Checking '{0}'".format(page)
            self.check_page_get(page, 200)

    def test_create_account_errors(self):
        # No post data -- should fail
        resp = self.client.post('/create_account', {})
        self.assertEqual(resp.status_code, 200)
        data = parse_json(resp)
        self.assertEqual(data['success'], False)

    def test_create_account(self):
        self.create_account(self.username, self.email, self.pw)
        self.activate_user(self.email)

    def test_login(self):
        self.create_account(self.username, self.email, self.pw)

        # Not activated yet.  Login should fail.
        resp = self._login(self.email, self.pw)
        data = parse_json(resp)
        self.assertFalse(data['success'])

        self.activate_user(self.email)

        # Now login should work
        self.login(self.email, self.pw)

    def test_private_pages_auth(self):
        """Make sure pages that do require login work."""
        auth_pages = (
            reverse('index'),
            )

        # These are pages that should just load when the user is logged in
        # (no data needed)
        simple_auth_pages = (
            reverse('index'),
            )

        # need an activated user
        self.test_create_account()

        # Create a new session
        self.client = Client()

        # Not logged in.  Should redirect to login.
        print 'Not logged in'
        for page in auth_pages:
            print "Checking '{0}'".format(page)
            self.check_page_get(page, expected=302)

        # Logged in should work.
        self.login(self.email, self.pw)

        print 'Logged in'
        for page in simple_auth_pages:
            print "Checking '{0}'".format(page)
            self.check_page_get(page, expected=200)

    def test_index_auth(self):

        # not logged in.  Should return a redirect.
        resp = self.client.get(reverse('index'))
        self.assertEqual(resp.status_code, 302)

        # Logged in should work.

TEST_DATA_MODULESTORE = copy.deepcopy(settings.MODULESTORE)
TEST_DATA_MODULESTORE['default']['OPTIONS']['fs_root'] = path('common/test/data')
TEST_DATA_MODULESTORE['direct']['OPTIONS']['fs_root'] = path('common/test/data')

@override_settings(MODULESTORE=TEST_DATA_MODULESTORE)
class EditTestCase(ContentStoreTestCase):
    """Check that editing functionality works on example courses"""

    def setUp(self):
        email = 'edit@test.com'
        password = 'foo'
        self.create_account('edittest', email, password)
        self.activate_user(email)
        self.login(email, password)
        xmodule.modulestore.django._MODULESTORES = {}
        xmodule.modulestore.django.modulestore().collection.drop()

    def check_edit_unit(self, test_course_name):
        import_from_xml(modulestore(), 'common/test/data/', [test_course_name])

        for descriptor in modulestore().get_items(Location(None, None, 'vertical', None, None)):
            print "Checking ", descriptor.location.url()
            print descriptor.__class__, descriptor.location
            resp = self.client.get(reverse('edit_unit', kwargs={'location': descriptor.location.url()}))
            self.assertEqual(resp.status_code, 200)

    def test_edit_unit_toy(self):
        self.check_edit_unit('toy')

    def test_edit_unit_full(self):
        self.check_edit_unit('full')

@override_settings(MODULESTORE=TEST_DATA_MODULESTORE)
class CourseCreationTest(ContentStoreTestCase):
    """Test new course creation code"""

    def setUp(self):
        uname = 'testuser'
        email = 'edit@test.com'
        password = 'foo'

        # Create the use so we can log them in.
        # Note that we do not actually need to do anything
        # for registration if we directly mark them active.
        u = User.objects.create_user(uname, email, password)
        u.is_active = True
        u.save()

        # Flush and initialize the module store
        # It needs a course template because it creates a new course
        # by cloning from the template.
        xmodule.modulestore.django._MODULESTORES = {}
        xmodule.modulestore.django.modulestore().collection.drop()
        template_item = {'_id': {'tag': 'i4x', 'org': 'edx', 'course': 'templates',
                         'category': 'course', 'name': 'Empty', 'revision': None}}
        xmodule.modulestore.django.modulestore().collection.insert(template_item)

        self.client = Client()
        self.client.login(username=uname, password=password)

        self.post_data = {
            'template': 'i4x://edx/templates/course/Empty',
            'org': 'MITx',
            'number': '999',
            'display_name': 'Robot Super Course',
        }

    def test_create_course(self):
        resp = self.client.post(reverse('create_new_course'), self.post_data)
        self.assertEqual(resp.status_code, 200)
        data = parse_json(resp)
        self.assertEqual(data['id'], 'i4x://MITx/999/course/Robot_Super_Course')

    def test_create_course_duplicate_course(self):
        resp = self.client.post(reverse('create_new_course'), self.post_data)
        resp = self.client.post(reverse('create_new_course'), self.post_data)
        data = parse_json(resp)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['ErrMsg'], 'There is already a course defined with this name.')

    def test_create_course_duplicate_number(self):
        resp = self.client.post(reverse('create_new_course'), self.post_data)
        self.post_data['display_name'] = 'Robot Super Course Two'

        resp = self.client.post(reverse('create_new_course'), self.post_data)
        data = parse_json(resp)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['ErrMsg'], 'There is already a course defined with the same organization and course number.')