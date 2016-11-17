import unittest
import warnings

import chainer
from chainer import testing
from chainer import utils


def f():
    utils.experimental('test.f')


def g():
    utils.experimental()


def h(x):
    utils.experimental()


class C(object):

    @staticmethod
    def static_method():
        utils.experimental()

    @classmethod
    def class_method(cls):
        utils.experimental()

    def __init__(self):
        utils.experimental()


class TestExperimental(unittest.TestCase):

    def setUp(self):
        self.original = chainer.disable_experimental_feature_warning
        chainer.disable_experimental_feature_warning = False

    def tearDown(self):
        chainer.disable_experimental_feature_warning = self.original

    def test_experimental_with_api_name(self):
        with warnings.catch_warnings(record=True) as w:
            f()

        self.assertEqual(len(w), 1)
        self.assertIs(w[0].category, FutureWarning)
        self.assertIn('test.f is an experimental API.', str(w[0].message))

    def test_experimental_with_no_api_name(self):
        with warnings.catch_warnings(record=True) as w:
            g()

        self.assertEqual(len(w), 1)
        self.assertIs(w[0].category, FutureWarning)
        self.assertIn('g is an experimental API.', str(w[0].message))

    def test_experimental_with_no_api_name_2(self):
        with warnings.catch_warnings(record=True) as w:
            C()

        self.assertEqual(len(w), 1)
        self.assertIs(w[0].category, FutureWarning)
        self.assertIn('C.__init__ is an experimental API.', str(w[0].message))

    def test_experimental_static_method(self):
        with warnings.catch_warnings(record=True) as w:
            C.static_method()

        self.assertEqual(len(w), 1)
        self.assertIs(w[0].category, FutureWarning)
        self.assertIn('static_method is an experimental API.', str(w[0].message))

    def test_experimental_class_method(self):
        with warnings.catch_warnings(record=True) as w:
            C.class_method()

        self.assertEqual(len(w), 1)
        self.assertIs(w[0].category, FutureWarning)
        self.assertIn('C.class_method is an experimental API.', str(w[0].message))


class TestExperimentalDuplicate(unittest.TestCase):

    def setUp(self):
        self.original = chainer.disable_experimental_feature_warning
        chainer.disable_experimental_feature_warning = False

    def tearDown(self):
        chainer.disable_experimental_feature_warning = self.original

    def test_multiple_same_function_calls(self):
        with warnings.catch_warnings(record=True) as w:
            f()
            f()

        self.assertEqual(len(w), 1)

    def test_different_functions(self):
        with warnings.catch_warnings(record=True) as w:
            f()
            g()

        self.assertEqual(len(w), 2)

    def test_multiple_same_class_instantiation(self):
        with warnings.catch_warnings(record=True) as w:
            C()
            C()

        self.assertEqual(len(w), 1)

    def test_multiple_calls_with_different_argument(self):
        with warnings.catch_warnings(record=True) as w:
            h(0)
            h(1)

        self.assertEqual(len(w), 1)


class TestDisableExperimentalWarning(unittest.TestCase):

    def setUp(self):
        self.original = chainer.disable_experimental_feature_warning
        chainer.disable_experimental_feature_warning = True

    def tearDown(self):
        chainer.disable_experimental_feature_warning = self.original

    def test_experimental(self):
        with warnings.catch_warnings(record=True) as w:
            f()

        self.assertEqual(len(w), 0)


testing.run_module(__name__, __file__)
