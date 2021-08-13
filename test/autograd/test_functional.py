import unittest
import warnings
import torch

import torch.autograd.functional as autogradF
from torch.testing._internal.common_cuda import TEST_CUDA
from torch.testing._internal.common_utils import (TestCase, run_tests, gradcheck,
                                                  gradgradcheck)

class TestAutogradFunctional(TestCase):
    def _assert_same_struct(self, res, base):
        # base and res should be Tensors or tuple of Tensors with the same size
        if isinstance(base, torch.Tensor):
            self.assertTrue(isinstance(res, torch.Tensor))
            self.assertEqual(base.size(), res.size())
        elif isinstance(base, tuple):
            self.assertTrue(isinstance(res, tuple))
            self.assertEqual(len(base), len(res))
            for el_base, el_res in zip(base, res):
                self.assertTrue(isinstance(el_base, torch.Tensor))
                self.assertTrue(isinstance(el_res, torch.Tensor))
                self.assertEqual(el_base.size(), el_res.size())
        else:
            # Wrong base
            raise RuntimeError("The base given to `_assert_same_struct` doesn't have"
                               " the right structure.")

    def _assert_interleaved_struct(self, res, base1, base2):
        # base1 and base2 can be Tensors or tuples of Tensors.
        # If they are tuples, res should be a tuple as well.
        # The indexing works as follows for base1, base2 being
        # - tuple, tuple: res[i][j][k][l] = (base1[i][k], base2[j][l])
        # - tuple, Tensor: res[i][k][l] = (base1[i][k], base2[l])
        # - Tensor, tuple: res[i][j][l] = (base1[i], base2[j][l])
        # - Tensor, Tensor: res[k][l] = (base1[k], base2[l])
        if isinstance(base1, torch.Tensor) and isinstance(base2, torch.Tensor):
            self.assertTrue(isinstance(res, torch.Tensor))
            self.assertEqual(res.size(), base1.size() + base2.size())
        elif isinstance(base1, tuple) and isinstance(base2, torch.Tensor):
            self.assertTrue(isinstance(res, tuple))
            self.assertEqual(len(res), len(base1))
            for el_res, el_base1 in zip(res, base1):
                self.assertTrue(isinstance(el_res, torch.Tensor))
                self.assertTrue(isinstance(el_base1, torch.Tensor))
                self.assertEqual(el_res.size(), el_base1.size() + base2.size())
        elif isinstance(base1, torch.Tensor) and isinstance(base2, tuple):
            self.assertTrue(isinstance(res, tuple))
            self.assertEqual(len(res), len(base2))
            for el_res, el_base2 in zip(res, base2):
                self.assertTrue(isinstance(el_res, torch.Tensor))
                self.assertTrue(isinstance(el_base2, torch.Tensor))
                self.assertEqual(el_res.size(), base1.size() + el_base2.size())
        elif isinstance(base1, tuple) and isinstance(base2, tuple):
            self.assertTrue(isinstance(res, tuple))
            self.assertEqual(len(res), len(base1))
            for el_res, el_base1 in zip(res, base1):
                self.assertTrue(isinstance(el_res, tuple))
                self.assertEqual(len(res), len(base2))
                for el_el_res, el_base2 in zip(el_res, base2):
                    self.assertTrue(isinstance(el_el_res, torch.Tensor))
                    self.assertTrue(isinstance(el_base2, torch.Tensor))
                    self.assertEqual(el_el_res.size(), el_base1.size() + el_base2.size())
        else:
            # Wrong bases
            raise RuntimeError("The bases given to `_assert_interleaved_struct` don't have"
                               " the right structure.")

    def test_vjp_err_check(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3)

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        inp = torch.rand(4)
        v = torch.ones(3)
        with self.assertRaisesRegex(TypeError, "The inputs given to vjp must be either a Tensor"):
            res = autogradF.vjp(foo, (inp, 2), v)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to vjp must"):
            res = autogradF.vjp(bar, inp, v)

        with self.assertRaisesRegex(RuntimeError, "The vector v can only be None if the user-provided function returns"):
            res = autogradF.vjp(foo, inp)

        with self.assertRaisesRegex(RuntimeError, "The given v should contain a single Tensor."):
            res = autogradF.vjp(foo, inp, (torch.ones_like(inp), torch.ones_like(inp)))

        with self.assertRaisesRegex(RuntimeError, "v has invalid size: should be torch.Size"):
            res = autogradF.vjp(foo, inp, v[:2])

        res = autogradF.vjp(foo, inp, v)[1]
        self._assert_same_struct(res, inp)

    def test_vjp_err_check_strict(self):
        def foo(a):
            return a.detach()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone()

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.vjp(foo, inp, v, strict=True)
        res = autogradF.vjp(foo, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "The output of the user-provided function is independent of input 0"):
            res = autogradF.vjp(bar, inp, v, strict=True)
        res = autogradF.vjp(bar, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        # The Jacobian does not depend on the input
        def foo(a):
            return a.clone()

        inp.requires_grad_()
        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function is independent of input 0."):
            res = autogradF.vjp(foo, inp, v, create_graph=True, strict=True)
        res = autogradF.vjp(foo, inp, v, create_graph=True, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1], v)

    def test_vjp_no_grad(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(4, 4)
        v = torch.ones(4)
        with torch.no_grad():
            res = autogradF.vjp(reducer, inputs, v)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

        inputs.requires_grad_()
        v.requires_grad_()
        with torch.no_grad():
            res = autogradF.vjp(reducer, inputs, v, create_graph=True)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

    def test_vjp_output(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(4, 4)
        v = torch.ones(4)
        res = autogradF.vjp(reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)

        def adder(x, y):
            return 2 * x + 3 * y

        inputs = (torch.rand(2), torch.rand(2))
        v = torch.ones(2)
        out, vjp_val = autogradF.vjp(adder, inputs, v)
        self._assert_same_struct(vjp_val, inputs)
        self.assertIsNone(out.grad_fn)
        self.assertIsNone(vjp_val[0].grad_fn)
        self.assertIsNone(vjp_val[1].grad_fn)

        def adder(x, y):
            return 2 * x + 3 * y, x + y

        inputs = (torch.rand(2), torch.rand(2))
        v = (torch.tensor([1., 0.]), torch.tensor([1., 0.]))
        out, vjp_val = autogradF.vjp(adder, inputs, v)
        self._assert_same_struct(vjp_val, inputs)
        self.assertIsNone(out[0].grad_fn)
        self.assertIsNone(out[1].grad_fn)
        self.assertIsNone(vjp_val[0].grad_fn)
        self.assertIsNone(vjp_val[1].grad_fn)

    def test_vjp_scalar(self):
        def reducer(x):
            return x.sum()
        inputs = torch.rand(4, 4)
        v = torch.ones([])
        res = autogradF.vjp(reducer, inputs, v)
        self._assert_same_struct(res[0], v)
        self._assert_same_struct(res[1], inputs)

        res = autogradF.vjp(reducer, inputs)
        self._assert_same_struct(res[0], v)
        self._assert_same_struct(res[1], inputs)

        def expander(x):
            return x.unsqueeze(0).repeat(4)
        inputs = torch.rand([])
        v = torch.ones(4)
        res = autogradF.vjp(expander, inputs, v)
        self._assert_same_struct(res[0], v)
        self._assert_same_struct(res[1], inputs)

    def test_vjp_create_graph(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(2, 2, dtype=torch.double)
        v = torch.ones(2, dtype=torch.double)

        inputs.requires_grad_()
        v.requires_grad_()
        res = autogradF.vjp(reducer, inputs, v, create_graph=True)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)

        gradcheck(lambda inp, v: autogradF.vjp(reducer, inputs, v, create_graph=True), (inputs, v))
        gradgradcheck(lambda inp, v: autogradF.vjp(reducer, inputs, v, create_graph=True), (inputs, v))

        def adder(x, y):
            return 2 * x + 3 * y, x * y

        inputs = (torch.rand(2, dtype=torch.double, requires_grad=True),
                  torch.rand(2, dtype=torch.double, requires_grad=True))
        v = (torch.tensor([1., 0.], dtype=torch.double, requires_grad=True),
             torch.tensor([1., 0.], dtype=torch.double, requires_grad=True))

        gradcheck(lambda *args: autogradF.vjp(adder, args[:2], args[2:], create_graph=True)[1], inputs + v)
        gradgradcheck(lambda *args: autogradF.vjp(adder, args[:2], args[2:], create_graph=True)[1], inputs + v)

        def foo(*args):
            x, y = args[:2]
            v = args[2:]

            x = x.cos()
            val, grad = autogradF.vjp(adder, (x, y), v, create_graph=True)

            return val[0].exp() + val[1].exp() + grad[0].exp() + grad[1].exp() + x.exp() + y.exp()

        gradcheck(foo, inputs + v)
        gradgradcheck(foo, inputs + v)

    def test_jvp_err_check(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3)

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(TypeError, "The inputs given to jvp must be either a Tensor"):
            res = autogradF.jvp(foo, (inp, 2), v)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to jvp must"):
            res = autogradF.jvp(bar, inp, v)

        with self.assertRaisesRegex(RuntimeError, "The vector v can only be None if the input to the user-provided function"):
            res = autogradF.jvp(foo, inp)

        with self.assertRaisesRegex(RuntimeError, "The given v should contain a single Tensor."):
            res = autogradF.jvp(foo, inp, (v, v))

        with self.assertRaisesRegex(RuntimeError, "v has invalid size: should be torch.Size"):
            res = autogradF.jvp(foo, inp, v[:2])

        res = autogradF.jvp(foo, inp, v)[1]
        self._assert_same_struct(res, foo(inp))

    def test_jvp_err_check_strict(self):
        def foo(a):
            return a.detach()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone()

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.jvp(foo, inp, v, strict=True)
        res = autogradF.jvp(foo, inp, v, strict=False)
        self._assert_same_struct(res[1], res[0])
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "The output of the user-provided function is independent of input 0"):
            res = autogradF.jvp(bar, inp, v, strict=True)
        res = autogradF.jvp(bar, inp, v, strict=False)
        self._assert_same_struct(res[1], res[0])
        self.assertEqual(res[1].abs().sum(), 0.)

        # The Jacobian does not depend on the input
        def foo(a):
            return a.clone()

        inp.requires_grad_()
        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function is independent of input 0."):
            res = autogradF.jvp(foo, inp, v, create_graph=True, strict=True)
        res = autogradF.jvp(foo, inp, v, create_graph=True, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1], v)

    def test_jvp_no_grad(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        with torch.no_grad():
            res = autogradF.jvp(reducer, inputs, v)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

        inputs.requires_grad_()
        v.requires_grad_()
        with torch.no_grad():
            res = autogradF.jvp(reducer, inputs, v, create_graph=True)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

    def test_jvp_output(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.jvp(reducer, inputs, v)
        self._assert_same_struct(res[1], res[0])
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)

        def adder(x, y):
            return 2 * x + 3 * y

        inputs = (torch.rand(2), torch.rand(2))
        v = (torch.ones(2), torch.ones(2))
        out, jvp_val = autogradF.jvp(adder, inputs, v)
        self._assert_same_struct(jvp_val, out)
        self.assertIsNone(out.grad_fn)
        self.assertIsNone(jvp_val[0].grad_fn)
        self.assertIsNone(jvp_val[1].grad_fn)

        def adder(x, y):
            return 2 * x + 3 * y, x + y

        inputs = (torch.rand(2), torch.rand(2))
        v = (torch.tensor([1., 0.]), torch.tensor([1., 0.]))
        out, jvp_val = autogradF.jvp(adder, inputs, v)
        self._assert_same_struct(jvp_val, out)
        self.assertIsNone(out[0].grad_fn)
        self.assertIsNone(out[1].grad_fn)
        self.assertIsNone(jvp_val[0].grad_fn)
        self.assertIsNone(jvp_val[1].grad_fn)

    def test_jvp_scalar(self):
        def reducer(x):
            return x.sum()
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.jvp(reducer, inputs, v)
        self._assert_same_struct(res[0], torch.zeros([]))
        self._assert_same_struct(res[1], res[0])

        def expander(x):
            return x.unsqueeze(0).repeat(4)
        inputs = torch.rand([])
        v = torch.ones([])
        res = autogradF.jvp(expander, inputs, v)
        self._assert_same_struct(res[0], torch.zeros(4))
        self._assert_same_struct(res[1], res[0])

        res = autogradF.jvp(expander, inputs)
        self._assert_same_struct(res[0], torch.zeros(4))
        self._assert_same_struct(res[1], res[0])

    def test_jvp_create_graph(self):
        def reducer(x):
            return x.sum(dim=1)
        inputs = torch.rand(2, 2, dtype=torch.double)
        v = torch.ones(2, 2, dtype=torch.double)

        inputs.requires_grad_()
        v.requires_grad_()
        res = autogradF.jvp(reducer, inputs, v, create_graph=True)
        self._assert_same_struct(res[1], res[0])
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)

        gradcheck(lambda inp, v: autogradF.jvp(reducer, inp, v, create_graph=True), (inputs, v))
        gradgradcheck(lambda inp, v: autogradF.jvp(reducer, inp, v, create_graph=True), (inputs, v))

        def adder(x, y):
            return 2 * x + 3 * y, x * y

        inputs = (torch.rand(2, dtype=torch.double, requires_grad=True),
                  torch.rand(2, dtype=torch.double, requires_grad=True))
        v = (torch.tensor([1., 0.], dtype=torch.double, requires_grad=True),
             torch.tensor([1., 0.], dtype=torch.double, requires_grad=True))

        gradcheck(lambda *args: autogradF.jvp(adder, args[:2], args[2:], create_graph=True)[1], inputs + v)
        gradgradcheck(lambda *args: autogradF.jvp(adder, args[:2], args[2:], create_graph=True)[1], inputs + v)

        def foo(*args):
            x, y = args[:2]
            v = args[2:]

            x = x.cos()
            val, grad = autogradF.jvp(adder, (x, y), v, create_graph=True)

            return val[0].exp() + val[1].exp() + grad[0].exp() + grad[1].exp() + x.exp() + y.exp()

        gradcheck(foo, inputs + v)
        gradgradcheck(foo, inputs + v)

    def _test_construct_standard_basis_for(self, inputs):
        numels = tuple(tensor.numel() for tensor in inputs)
        results = autogradF._construct_standard_basis_for(inputs, numels)
        for result, inp in zip(results, inputs):
            self.assertEqual(result.dtype, inp.dtype)
            self.assertEqual(result.device, inp.device)
        results = torch.cat([result.to(device='cpu', dtype=torch.float)
                             for result in results], dim=1)
        expected = torch.eye(results[0].shape[0], dtype=torch.float)
        self.assertEqual(results, expected)

    def test_construct_standard_basis_for(self):
        test_cases = [
            (torch.randn(2, 3),),
            (torch.randn(1),),
            (torch.randn([]),),
            (torch.randn(1), torch.randn([]), torch.randn([])),
            (torch.randn(2), torch.randn(3), torch.randn([])),
            (torch.randn(2), torch.randn([]), torch.randn(3)),
            (torch.randn(2, 3), torch.randn(3), torch.randn(3, 4, 2)),
            (torch.randn(2, dtype=torch.float64), torch.randn(3, dtype=torch.float32)),
        ]

        for inputs in test_cases:
            self._test_construct_standard_basis_for(inputs)

    @unittest.skipIf(not TEST_CUDA, "test requires CUDA")
    def test_construct_standard_basis_for_cuda(self):
        test_cases = [
            (torch.randn(2), torch.randn(3, device='cuda')),
            (torch.randn(3, device='cuda'), torch.randn(2)),
        ]

        for inputs in test_cases:
            self._test_construct_standard_basis_for(inputs)

    def _test_vectorize_raises_no_warnings(self, api):
        # vmap is an experimental prototype. When someone calls torch.vmap,
        # it raises a python warning. This test checks that
        # autogradF.{jacobian, hessian} don't raise that experimental prototype
        # warning; it is not nice for a public-facing API to raise a warning
        # no matter how it is called.
        def foo(a):
            return (a ** 2).sum()

        x = torch.randn(3)
        with warnings.catch_warnings(record=True) as wa:
            result = api(foo, x, vectorize=True)
        self.assertEqual(len(wa), 0)

    def test_jacobian_vectorize_raises_no_warnings(self):
        return self._test_vectorize_raises_no_warnings(autogradF.jacobian)

    def test_hessian_vectorize_raises_no_warnings(self):
        return self._test_vectorize_raises_no_warnings(autogradF.hessian)

    def _test_jacobian_err_check(self, vectorize):
        def foo(a):
            return 3 * a.narrow(0, 0, 3)

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        inp = torch.rand(4)
        with self.assertRaisesRegex(TypeError, "The inputs given to jacobian must be either a Tensor"):
            res = autogradF.jacobian(foo, (inp, 2), vectorize=vectorize)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to jacobian must"):
            res = autogradF.jacobian(bar, inp, vectorize=vectorize)

        res = autogradF.jacobian(foo, inp, vectorize=vectorize)
        self._assert_interleaved_struct(res, foo(inp), inp)

        def foo(a, b):
            return b, 3 * a.narrow(0, 0, 3)

        inp = (torch.rand(4), torch.rand(5))

        res = autogradF.jacobian(foo, inp, vectorize=vectorize)
        self._assert_interleaved_struct(res, foo(*inp), inp)

    def test_jacobian_err_check(self):
        return self._test_jacobian_err_check(vectorize=False)

    def test_jacobian_err_check_vectorize(self):
        return self._test_jacobian_err_check(vectorize=True)

    def test_jacobian_err_check_strict(self):
        def foo(a):
            return a.detach()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone()

        inp = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.jacobian(foo, inp, strict=True)
        res = autogradF.jacobian(foo, inp, strict=False)
        self._assert_interleaved_struct(res, foo(inp), inp)
        self.assertEqual(res.abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function is independent of input 0."):
            res = autogradF.jacobian(bar, inp, strict=True)
        res = autogradF.jacobian(bar, inp, strict=False)
        self._assert_interleaved_struct(res, foo(inp), inp)
        self.assertEqual(res.abs().sum(), 0.)

        # The Jacobian does not depend on the input
        def foo(a):
            return a.clone()

        inp.requires_grad_()
        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function is independent of input 0."):
            res = autogradF.jacobian(foo, inp, create_graph=True, strict=True)
        res = autogradF.jacobian(foo, inp, create_graph=True, strict=False)
        self._assert_interleaved_struct(res, inp, inp)
        self.assertEqual(res, torch.eye(4))

    def test_jacobian_err_check_strict_vectorize(self):
        def foo(x):
            return x

        inp = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "not supported together"):
            res = autogradF.jacobian(foo, inp, strict=True, vectorize=True)

    def test_jacobian_no_grad(self):
        def exp_reducer(x):
            return x.exp().sum(dim=1)

        inputs = torch.rand(4, 4)
        with torch.no_grad():
            res = autogradF.jacobian(exp_reducer, inputs)
        self.assertIsNone(res.grad_fn)
        self.assertNotEqual(res, torch.zeros(4, 4))

        with torch.no_grad():
            res = autogradF.jacobian(exp_reducer, inputs, create_graph=True)
        self.assertIsNotNone(res.grad_fn)
        self.assertNotEqual(res, torch.zeros(4, 4))

    def _test_jacobian_output(self, vectorize):
        def exp_reducer(x):
            return x.exp().sum(dim=1)

        inputs = torch.rand(4, 4)
        res = autogradF.jacobian(exp_reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, exp_reducer(inputs), inputs)
        self.assertIsNone(res.grad_fn)

        def identity(x):
            return x.clone()

        inputs = torch.rand(4)
        res = autogradF.jacobian(identity, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, identity(inputs), inputs)
        self.assertIsNone(res.grad_fn)
        self.assertEqual(res, torch.eye(4))

        def add_exp_reducer(x, y):
            return (x + y.exp()).sum(dim=1)

        inputs = (torch.rand(4, 4), torch.rand(4, 4))
        res = autogradF.jacobian(add_exp_reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, add_exp_reducer(*inputs), inputs)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)

    def test_jacobian_output(self):
        self._test_jacobian_output(vectorize=False)

    def test_jacobian_output_vectorize(self):
        self._test_jacobian_output(vectorize=True)

    def _test_jacobian_scalar(self, vectorize):
        def reducer(x):
            return x.sum()
        inputs = torch.rand(4, 4)
        res = autogradF.jacobian(reducer, inputs, vectorize=vectorize)
        self._assert_same_struct(res, inputs)

        def expander(x):
            return x.unsqueeze(0).repeat(4)
        inputs = torch.rand([])
        res = autogradF.jacobian(expander, inputs, vectorize=vectorize)
        self._assert_same_struct(res, torch.zeros(4))

    def test_jacobian_scalar(self):
        self._test_jacobian_scalar(vectorize=False)

    def test_jacobian_scalar_vectorize(self):
        self._test_jacobian_scalar(vectorize=True)

    def _test_jacobian_create_graph(self, vectorize):
        def exp_reducer(x):
            return x.exp().sum(dim=1)

        inputs = torch.rand(4, 4, dtype=torch.double, requires_grad=True)
        res = autogradF.jacobian(exp_reducer, inputs, create_graph=True, vectorize=vectorize)
        self._assert_interleaved_struct(res, exp_reducer(inputs), inputs)
        self.assertIsNotNone(res.grad_fn)

        gradcheck(lambda inp: autogradF.jacobian(exp_reducer, inp, create_graph=True, vectorize=vectorize), inputs)
        gradgradcheck(lambda inp: autogradF.jacobian(exp_reducer, inp, create_graph=True, vectorize=vectorize), inputs)

        def add_exp_reducer(x, y):
            return (x + y).exp().sum(dim=1)

        inputs = (torch.rand(4, 4, dtype=torch.double, requires_grad=True),
                  torch.rand(4, 4, dtype=torch.double, requires_grad=True))
        res = autogradF.jacobian(add_exp_reducer, inputs, create_graph=True, vectorize=vectorize)
        self._assert_interleaved_struct(res, add_exp_reducer(*inputs), inputs)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)

        gradcheck(lambda *inp: autogradF.jacobian(add_exp_reducer, inp, create_graph=True, vectorize=vectorize), inputs)
        gradgradcheck(lambda *inp: autogradF.jacobian(add_exp_reducer, inp, create_graph=True, vectorize=vectorize), inputs)

        def foo(x, y):
            x = x.cos()
            val, jac = autogradF.jacobian(add_exp_reducer, (x, y), create_graph=True, vectorize=vectorize)

            res = val[0].exp().sum() + val[1].exp().sum() + jac[0].exp().sum()
            res = res + jac[1].exp().sum() + x.exp().sum() + y.exp().sum()
            return res

        gradcheck(foo, inputs)
        gradgradcheck(foo, inputs)

    def test_jacobian_create_graph(self):
        self._test_jacobian_create_graph(vectorize=False)

    def test_jacobian_create_graph_vectorize(self):
        self._test_jacobian_create_graph(vectorize=True)

    def _check_jacobian_vectorize_correctness(self, f, inputs):
        expected = autogradF.jacobian(f, inputs, vectorize=False)
        result = autogradF.jacobian(f, inputs, vectorize=True)
        self.assertEqual(result, expected)

    def test_jacobian_vectorize_correctness_simple(self):
        def f(x):
            return 3 * x ** 2

        x = torch.randn(2, 3, 5)
        self._check_jacobian_vectorize_correctness(f, x)

    def test_jacobian_vectorize_correctness_multi_input(self):
        def f(x, y):
            return (x.cos() * x) @ y.sin()

        x = torch.randn(2, 3)
        y = torch.randn(3, 5)
        self._check_jacobian_vectorize_correctness(f, (x, y))

    def test_jacobian_vectorize_correctness_multi_input_multi_output(self):
        def f(x, y):
            return (x * x) @ y, x @ (x.sum(1) * y), y.sum()

        x = torch.randn(5, 3)
        y = torch.randn(3, 5)
        self._check_jacobian_vectorize_correctness(f, (x, y))

    def test_jacobian_vectorize_correctness_unrelated_outputs(self):
        def f(x, y):
            return x, y, x, y

        x = torch.randn(2)
        y = torch.randn(3)
        self._check_jacobian_vectorize_correctness(f, (x, y))

    def test_jacobian_vectorize_correctness_zero_dim(self):
        # zero-dim output
        def f(x, y):
            return x.sum(), y.sum(), x * y

        x = torch.randn(3)
        y = torch.randn(3)
        self._check_jacobian_vectorize_correctness(f, (x, y))

        # zero-dim input
        def g(x):
            return torch.stack([x, x, x])

        x = torch.randn([])
        self._check_jacobian_vectorize_correctness(g, x)

        # Mixed zero-dim input / zero-dim output
        def h(x, y):
            return y.sum(), x * y

        x = torch.randn([])
        y = torch.randn(1)
        self._check_jacobian_vectorize_correctness(h, (x, y))

    @unittest.skipIf(not TEST_CUDA, "test requires CUDA")
    def test_jacobian_vectorize_correctness_different_devices(self):
        def f(x, y):
            return x * y, (x * y).cuda()

        x = torch.randn(3)
        y = torch.randn(3)
        self._check_jacobian_vectorize_correctness(f, (x, y))

    def test_jacobian_vectorize_correctness_different_dtype(self):
        def f(x, y):
            return (x * y).float(), (x * y).double()

        x = torch.randn(3)
        y = torch.randn(3)
        self._check_jacobian_vectorize_correctness(f, (x, y))

    def _check_hessian_vectorize_correctness(self, f, inputs):
        expected = autogradF.hessian(f, inputs, vectorize=False)
        result = autogradF.hessian(f, inputs, vectorize=True)
        self.assertEqual(result, expected)

    def test_hessian_vectorize_correctness_simple(self):
        def f(x):
            return (3 * x ** 2).sum()

        x = torch.randn(2, 3, 5)
        self._check_hessian_vectorize_correctness(f, x)

    def test_hessian_vectorize_correctness_multi_input(self):
        def f(x, y, z):
            return ((x.relu() * x) @ y.sin() @ z).sum()

        x = torch.randn(2, 3)
        y = torch.randn(3, 5)
        z = torch.randn(5, 5)
        self._check_hessian_vectorize_correctness(f, (x, y, z))

    def test_hessian_vectorize_correctness_unrelated_outputs(self):
        # output unrelated to one input
        def f(x, y):
            return (x ** 2).sum()

        x = torch.randn(2)
        y = torch.randn(3)
        self._check_hessian_vectorize_correctness(f, (x, y))

        # output unrelated to all inputs
        def f(x, y):
            return torch.randn([])

        x = torch.randn(2)
        y = torch.randn(3)
        self._check_hessian_vectorize_correctness(f, (x, y))

    def _test_hessian_err_check(self, vectorize):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        def bar2(a):
            return 3 * a.narrow(0, 0, 3)

        def bar3(a):
            return 3 * a.narrow(0, 0, 3), 3 * a.narrow(0, 0, 3)

        inp = torch.rand(4)
        with self.assertRaisesRegex(TypeError, "The inputs given to hessian must be either a Tensor"):
            res = autogradF.hessian(foo, (inp, 2), vectorize=vectorize)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to hessian must"):
            res = autogradF.hessian(bar, inp, vectorize=vectorize)

        err_msg_out = "The Tensor returned by the function given to hessian should contain a single element"
        with self.assertRaisesRegex(RuntimeError, err_msg_out):
            res = autogradF.hessian(bar2, inp, vectorize=vectorize)

        with self.assertRaisesRegex(RuntimeError, "The function given to hessian should return a single Tensor"):
            res = autogradF.hessian(bar3, inp, vectorize=vectorize)

        res = autogradF.hessian(foo, inp, vectorize=vectorize)
        self._assert_interleaved_struct(res, inp, inp)

        def foo(a, b):
            return (3 * b.narrow(0, 0, 3) * a.narrow(0, 0, 3)).sum()

        inp = (torch.rand(4), torch.rand(5))

        res = autogradF.hessian(foo, inp, vectorize=vectorize)
        self._assert_interleaved_struct(res, inp, inp)

    def test_hessian_err_check(self):
        self._test_hessian_err_check(vectorize=False)

    def test_hessian_err_check_vectorize(self):
        self._test_hessian_err_check(vectorize=True)

    def test_hessian_err_check_strict(self):
        def foo(a):
            return a.detach().sum()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone().sum()

        def bar2(a):
            # A Linear function for which the jacobian is independent of the input
            return (3 * a).sum()

        inp = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.hessian(foo, inp, strict=True)
        res = autogradF.hessian(foo, inp, strict=False)
        self._assert_interleaved_struct(res, inp, inp)
        self.assertEqual(res.abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function with respect to input 0"):
            res = autogradF.hessian(bar, inp, strict=True)
        res = autogradF.hessian(bar, inp, strict=False)
        self._assert_interleaved_struct(res, inp, inp)
        self.assertEqual(res.abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function with respect to input 0 is"):
            res = autogradF.hessian(bar2, inp, strict=True)
        res = autogradF.hessian(bar2, inp, strict=False)
        self._assert_interleaved_struct(res, inp, inp)
        self.assertEqual(res.abs().sum(), 0.)

    def test_hessian_err_check_strict_vectorize(self):
        def foo(x):
            return (x ** 3).sum()

        inp = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "not supported together"):
            res = autogradF.hessian(foo, inp, strict=True, vectorize=True)

    def test_hessian_no_grad(self):
        def pow_reducer(x):
            return x.pow(3).sum()

        inputs = torch.rand(2, 2)
        with torch.no_grad():
            res = autogradF.hessian(pow_reducer, inputs)
        self.assertIsNone(res[0][0].grad_fn)
        self.assertIsNone(res[0][1].grad_fn)
        self.assertIsNone(res[1][0].grad_fn)
        self.assertIsNone(res[1][1].grad_fn)
        self.assertNotEqual(res, torch.zeros(2, 2, 2))

        with torch.no_grad():
            res = autogradF.hessian(pow_reducer, inputs, create_graph=True)
        self.assertIsNotNone(res[0][0].grad_fn)
        self.assertIsNotNone(res[0][1].grad_fn)
        self.assertIsNotNone(res[1][0].grad_fn)
        self.assertIsNotNone(res[1][1].grad_fn)
        self.assertNotEqual(res, torch.zeros(2, 2, 2))


    def _test_hessian_output(self, vectorize):
        def pow_reducer(x):
            return x.pow(3).sum()

        inputs = torch.rand(2, 2)
        res = autogradF.hessian(pow_reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)
        self.assertIsNone(res.grad_fn)

        def add_pow_reducer(x, y):
            return (x + y).pow(3).sum()

        inputs = (torch.rand(2, 2), torch.rand(2, 2))
        res = autogradF.hessian(add_pow_reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)
        self.assertIsNone(res[0][0].grad_fn)
        self.assertIsNone(res[0][1].grad_fn)
        self.assertIsNone(res[1][0].grad_fn)
        self.assertIsNone(res[1][1].grad_fn)

    def test_hessian_output(self):
        self._test_hessian_output(vectorize=False)

    def test_hessian_output_vectorize(self):
        self._test_hessian_output(vectorize=True)

    def _test_hessian_scalar(self, vectorize):
        def reducer(x):
            return x.sum()
        inputs = torch.rand(4, 4)
        res = autogradF.hessian(reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)

        inputs = torch.rand([])
        res = autogradF.hessian(reducer, inputs, vectorize=vectorize)
        self._assert_same_struct(res, inputs)

        def bad_reducer(x):
            return x.sum().view(1, 1, 1)
        inputs = torch.rand(4, 4)
        res = autogradF.hessian(bad_reducer, inputs, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)

    def test_hessian_scalar(self):
        return self._test_hessian_scalar(vectorize=False)

    def test_hessian_scalar_vectorize(self):
        return self._test_hessian_scalar(vectorize=True)

    def _test_hessian_create_graph(self, vectorize):
        def pow_reducer(x):
            return x.pow(3).sum()

        inputs = torch.rand(2, 2, dtype=torch.double, requires_grad=True)
        res = autogradF.hessian(pow_reducer, inputs, create_graph=True, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)
        self.assertIsNotNone(res.grad_fn)

        gradcheck(lambda inp: autogradF.hessian(pow_reducer, inp, create_graph=True, vectorize=vectorize), inputs)
        gradgradcheck(lambda inp: autogradF.hessian(pow_reducer, inp, create_graph=True, vectorize=vectorize), inputs)

        def add_pow_reducer(x, y):
            return (x + y).pow(3).sum()

        inputs = (torch.rand(2, 2, dtype=torch.double, requires_grad=True),
                  torch.rand(2, 2, dtype=torch.double, requires_grad=True))
        res = autogradF.hessian(add_pow_reducer, inputs, create_graph=True, vectorize=vectorize)
        self._assert_interleaved_struct(res, inputs, inputs)
        self.assertIsNotNone(res[0][0].grad_fn)
        self.assertIsNotNone(res[0][1].grad_fn)
        self.assertIsNotNone(res[1][0].grad_fn)
        self.assertIsNotNone(res[1][1].grad_fn)

        def flatten(inp):
            return tuple(el_lvl2 for el_lvl1 in inp for el_lvl2 in el_lvl1)

        gradcheck(lambda *inp: flatten(autogradF.hessian(add_pow_reducer, inp, create_graph=True, vectorize=vectorize)), inputs)
        gradgradcheck(lambda *inp: flatten(autogradF.hessian(add_pow_reducer, inp, create_graph=True, vectorize=vectorize)), inputs)

        def foo(x, y):
            x = x.cos()
            val, hess = autogradF.hessian(add_pow_reducer, (x, y), create_graph=True, vectorize=vectorize)

            res = val[0].cos().sum() + val[1].cos().sum() + hess[0].cos().sum()
            res = res + hess[1].cos().sum() + x.cos().sum() + y.cos().sum()
            return res

        gradcheck(foo, inputs)
        gradgradcheck(foo, inputs)

    def test_hessian_create_graph(self):
        self._test_hessian_create_graph(vectorize=False)

    def test_hessian_create_graph_vectorize(self):
        self._test_hessian_create_graph(vectorize=True)

    def test_vhp_err_check(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        def bar2(a):
            return 3 * a.narrow(0, 0, 3)

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(TypeError, "The inputs given to vhp must be either a Tensor"):
            res = autogradF.vhp(foo, (inp, 2), v)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to vhp must"):
            res = autogradF.vhp(bar, inp, v)

        err_msg_out = "The Tensor returned by the function given to vhp should contain a single element"
        with self.assertRaisesRegex(RuntimeError, err_msg_out):
            res = autogradF.vhp(bar2, inp, v)

        with self.assertRaisesRegex(RuntimeError, "v has invalid size:"):
            res = autogradF.vhp(foo, inp, torch.rand(5))

        with self.assertRaisesRegex(TypeError, "The v given to vhp must be either a Tensor or a tuple of Tensors"):
            res = autogradF.vhp(foo, inp, (v, 2))

        res = autogradF.vhp(foo, inp, v)
        self._assert_same_struct(res[1], inp)

        def foo(a, b):
            return (3 * b.narrow(0, 0, 3) * a.narrow(0, 0, 3)).sum()

        inp = (torch.rand(4), torch.rand(5))
        v = (torch.rand(4), torch.rand(5))

        res = autogradF.vhp(foo, inp, v)
        self._assert_same_struct(res[1], inp)

    def test_vhp_err_check_strict(self):
        def foo(a):
            return a.detach().sum()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone().sum()

        def bar2(a):
            # A Linear function for which the jacobian is independent of the input
            return (3 * a).sum()

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.vhp(foo, inp, v, strict=True)
        res = autogradF.vhp(foo, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "The output of the user-provided function is independent of input 0"):
            res = autogradF.vhp(bar, inp, v, strict=True)
        res = autogradF.vhp(bar, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function with respect to input 0 is"):
            res = autogradF.vhp(bar2, inp, v, strict=True)
        res = autogradF.vhp(bar2, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

    def test_vhp_no_grad(self):
        def reducer(x):
            return x.exp().sum()
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        with torch.no_grad():
            res = autogradF.vhp(reducer, inputs, v)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

        with torch.no_grad():
            res = autogradF.vhp(reducer, inputs, v, create_graph=True)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

    def test_vhp_output(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.vhp(foo, inputs, v)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)

        def bar(a, b):
            return (a + 3 * b.narrow(0, 0, 3)).exp().sum()

        inputs = (torch.rand(3), torch.rand(4))
        v = (torch.ones(3), torch.ones(4))
        out, vhp_val = autogradF.vhp(bar, inputs, v)
        self._assert_same_struct(vhp_val, inputs)
        self.assertIsNone(out.grad_fn)
        self.assertIsNone(vhp_val[0].grad_fn)
        self.assertIsNone(vhp_val[1].grad_fn)

    def test_vhp_scalar(self):
        def reducer(x):
            return x.sum()
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.vhp(reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

        inputs = torch.rand([])
        v = torch.rand([])
        res = autogradF.vhp(reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

        res = autogradF.vhp(reducer, inputs)
        self._assert_same_struct(res[1], inputs)

        def bad_reducer(x):
            return x.sum().view(1, 1, 1)
        inputs = torch.rand(4, 4)
        v = torch.rand(4, 4)
        res = autogradF.vhp(bad_reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

    def test_vhp_create_graph(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        inputs = torch.rand(4, 4, dtype=torch.double, requires_grad=True)
        v = torch.ones(4, 4, dtype=torch.double, requires_grad=True)
        res = autogradF.vhp(foo, inputs, v, create_graph=True)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)

        gradcheck(lambda inp, v: autogradF.vhp(foo, inp, v, create_graph=True), (inputs, v))
        gradgradcheck(lambda inp, v: autogradF.vhp(foo, inp, v, create_graph=True), (inputs, v))

        def bar(a, b):
            return (a + 3 * b.narrow(0, 0, 3)).exp().sum()

        inputs = (torch.rand(3, dtype=torch.double, requires_grad=True),
                  torch.rand(4, dtype=torch.double, requires_grad=True))
        v = (torch.ones(3, dtype=torch.double, requires_grad=True),
             torch.ones(4, dtype=torch.double, requires_grad=True))
        out, vhp_val = autogradF.vhp(bar, inputs, v, create_graph=True)
        self._assert_same_struct(vhp_val, inputs)
        self.assertIsNotNone(out.grad_fn)
        self.assertIsNotNone(vhp_val[0].grad_fn)
        self.assertIsNotNone(vhp_val[1].grad_fn)

        gradcheck(lambda *args: autogradF.vhp(bar, args[:2], args[2:], create_graph=True)[1], inputs + v)
        gradgradcheck(lambda *args: autogradF.vhp(bar, args[:2], args[2:], create_graph=True)[1], inputs + v)

        def foo(*args):
            x, y = args[:2]
            v = args[2:]

            x = x.cos()
            val, grad = autogradF.vhp(bar, (x, y), v, create_graph=True)

            return val.cos() + grad[0].cos().sum() + grad[1].cos() + x.cos().sum() + y.cos()

        gradcheck(foo, inputs + v)
        gradgradcheck(foo, inputs + v)

    def test_hvp_err_check(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        def bar(a):
            return 3 * a.narrow(0, 0, 3), "bar"

        def bar2(a):
            return 3 * a.narrow(0, 0, 3)

        inp = torch.rand(4)
        v = torch.rand(4)
        res = autogradF.hvp(foo, inp, v)
        with self.assertRaisesRegex(TypeError, "The inputs given to hvp must be either a Tensor"):
            res = autogradF.hvp(foo, (inp, 2), v)

        with self.assertRaisesRegex(TypeError, "The outputs of the user-provided function given to hvp must"):
            res = autogradF.hvp(bar, inp, v)

        err_msg_out = "The Tensor returned by the function given to hvp should contain a single element"
        with self.assertRaisesRegex(RuntimeError, err_msg_out):
            res = autogradF.hvp(bar2, inp, v)

        with self.assertRaisesRegex(RuntimeError, "v has invalid size:"):
            res = autogradF.hvp(foo, inp, torch.rand(5))

        with self.assertRaisesRegex(TypeError, "The v given to hvp must be either a Tensor or a tuple of Tensors"):
            res = autogradF.hvp(foo, inp, (v, 2))

        res = autogradF.hvp(foo, inp, v)
        self._assert_same_struct(res[1], inp)

        def foo(a, b):
            return (3 * b.narrow(0, 0, 3) * a.narrow(0, 0, 3)).sum()

        inp = (torch.rand(4), torch.rand(5))
        v = (torch.rand(4), torch.rand(5))

        res = autogradF.hvp(foo, inp, v)
        self._assert_same_struct(res[1], inp)

    def test_hvp_err_check_strict(self):
        def foo(a):
            return a.detach().sum()

        def bar(a):
            # Make a non-leaf Tensor that requires_grad but that is not connected to the input
            return a.long().float().requires_grad_().clone().sum()

        def bar2(a):
            # A Linear function for which the jacobian is independent of the input
            return (3 * a).sum()

        inp = torch.rand(4)
        v = torch.rand(4)
        with self.assertRaisesRegex(RuntimeError, "Output 0 of the user-provided function does not require gradients."):
            res = autogradF.hvp(foo, inp, v, strict=True)
        res = autogradF.hvp(foo, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "The output of the user-provided function is independent of input 0"):
            res = autogradF.hvp(bar, inp, v, strict=True)
        res = autogradF.hvp(bar, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

        with self.assertRaisesRegex(RuntimeError, "jacobian of the user-provided function with respect to input 0 is"):
            res = autogradF.hvp(bar2, inp, v, strict=True)
        res = autogradF.hvp(bar2, inp, v, strict=False)
        self._assert_same_struct(res[1], inp)
        self.assertEqual(res[1].abs().sum(), 0.)

    def test_hvp_no_grad(self):
        def reducer(x):
            return x.exp().sum()
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        with torch.no_grad():
            res = autogradF.hvp(reducer, inputs, v)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

        with torch.no_grad():
            res = autogradF.hvp(reducer, inputs, v, create_graph=True)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)
        self.assertNotEqual(res[1], torch.zeros(4, 4))

    def test_hvp_output(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.hvp(foo, inputs, v)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNone(res[0].grad_fn)
        self.assertIsNone(res[1].grad_fn)

        def bar(a, b):
            return (a + 3 * b.narrow(0, 0, 3)).exp().sum()

        inputs = (torch.rand(3), torch.rand(4))
        v = (torch.ones(3), torch.ones(4))
        out, hvp_val = autogradF.hvp(bar, inputs, v)
        self._assert_same_struct(hvp_val, inputs)
        self.assertIsNone(out.grad_fn)
        self.assertIsNone(hvp_val[0].grad_fn)
        self.assertIsNone(hvp_val[1].grad_fn)

    def test_hvp_scalar(self):
        def reducer(x):
            return x.exp().sum()
        inputs = torch.rand(4, 4)
        v = torch.ones(4, 4)
        res = autogradF.hvp(reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

        inputs = torch.rand([])
        v = torch.rand([])
        res = autogradF.hvp(reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

        res = autogradF.hvp(reducer, inputs)
        self._assert_same_struct(res[1], inputs)

        def bad_reducer(x):
            return x.exp().sum().view(1, 1, 1)
        inputs = torch.rand(4, 4)
        v = torch.rand(4, 4)
        res = autogradF.hvp(bad_reducer, inputs, v)
        self._assert_same_struct(res[1], inputs)

    def test_hvp_create_graph(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        inputs = torch.rand(4, 4, dtype=torch.double, requires_grad=True)
        v = torch.ones(4, 4, dtype=torch.double, requires_grad=True)
        res = autogradF.hvp(foo, inputs, v, create_graph=True)
        self._assert_same_struct(res[1], inputs)
        self.assertIsNotNone(res[0].grad_fn)
        self.assertIsNotNone(res[1].grad_fn)

        gradcheck(lambda inp, v: autogradF.hvp(foo, inp, v, create_graph=True), (inputs, v))
        gradgradcheck(lambda inp, v: autogradF.hvp(foo, inp, v, create_graph=True), (inputs, v))

        def bar(a, b):
            return (a + 3 * b.narrow(0, 0, 3)).exp().sum()

        inputs = (torch.rand(3, dtype=torch.double, requires_grad=True),
                  torch.rand(4, dtype=torch.double, requires_grad=True))
        v = (torch.ones(3, dtype=torch.double, requires_grad=True),
             torch.ones(4, dtype=torch.double, requires_grad=True))
        out, hvp_val = autogradF.hvp(bar, inputs, v, create_graph=True)
        self._assert_same_struct(hvp_val, inputs)
        self.assertIsNotNone(out.grad_fn)
        self.assertIsNotNone(hvp_val[0].grad_fn)
        self.assertIsNotNone(hvp_val[1].grad_fn)

        gradcheck(lambda *args: autogradF.hvp(bar, args[:2], args[2:], create_graph=True)[1], inputs + v)
        gradgradcheck(lambda *args: autogradF.hvp(bar, args[:2], args[2:], create_graph=True)[1], inputs + v)

        def foo(*args):
            x, y = args[:2]
            v = args[2:]

            x = x.cos()
            val, grad = autogradF.hvp(bar, (x, y), v, create_graph=True)

            return val.cos() + grad[0].cos().sum() + grad[1].cos() + x.cos().sum() + y.cos()

        gradcheck(foo, inputs + v)
        gradgradcheck(foo, inputs + v)

    def test_jacobian_match_vjp_jvp(self):
        def foo(x):
            return x ** 3 + x.sum()

        inputs = torch.rand(4)
        v = torch.rand(4)

        jac = autogradF.jacobian(foo, inputs)
        jvp = autogradF.jvp(foo, inputs, v)[1]
        vjp = autogradF.vjp(foo, inputs, v)[1]

        self.assertEqual(jvp, torch.mm(jac, v.unsqueeze(1)).squeeze(1))
        self.assertEqual(vjp, torch.mm(v.unsqueeze(0), jac).squeeze(0))

    def test_hessian_match_vhp_hvp(self):
        def foo(a):
            return 3 * a.narrow(0, 0, 3).exp().sum()

        inputs = torch.rand(4)
        v = torch.rand(4)

        hes = autogradF.hessian(foo, inputs)
        hvp = autogradF.hvp(foo, inputs, v)[1]
        vhp = autogradF.vhp(foo, inputs, v)[1]

        self.assertEqual(hvp, torch.mm(hes, v.unsqueeze(1)).squeeze(1))
        self.assertEqual(vhp, torch.mm(v.unsqueeze(0), hes).squeeze(0))

if __name__ == '__main__':
    run_tests()