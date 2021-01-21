import atexit
import re
import shutil
import tempfile
import textwrap
from typing import Iterator, List, Optional, Tuple

from core.api import AutogradMode, AutoLabels, RuntimeMode, TimerArgs, GroupedBenchmark
from core.types import Definition, FlatDefinition, FlatIntermediateDefinition, Label
from worker.main import WorkerTimerArgs


def _flatten(
    key_prefix: Label,
    sub_schema: Definition,
    result: FlatIntermediateDefinition
) -> None:
    for k, value in sub_schema.items():
        if isinstance(k, tuple):
            assert all(isinstance(ki, str) for ki in k)
            key_suffix: Label = k
        elif k is None:
            key_suffix = ()
        else:
            assert isinstance(k, str)
            key_suffix = (k,)

        key: Label = key_prefix + key_suffix
        if isinstance(value, (TimerArgs, GroupedBenchmark)):
            assert key not in result, f"duplicate key: {key}"
            result[key] = value
        else:
            assert isinstance(value, dict)
            _flatten(key_prefix=key, sub_schema=value, result=result)


def flatten(schema: Definition) -> FlatIntermediateDefinition:
    result: FlatIntermediateDefinition = {}
    _flatten(key_prefix=(), sub_schema=schema, result=result)

    # Ensure that we produced a valid flat definition.
    for k, v in result.items():
        assert isinstance(k, tuple)
        assert all(isinstance(ki, str) for ki in k)
        assert isinstance(v, (TimerArgs, GroupedBenchmark))
    return result


def unpack(definitions: FlatIntermediateDefinition) -> FlatDefinition:
    results: List[Tuple[Label, AutoLabels, TimerArgs]] = []

    for label, args in definitions.items():
        if isinstance(args, TimerArgs):
            auto_labels = AutoLabels(
                RuntimeMode.EXPLICIT,
                AutogradMode.EXPLICIT,
                args.language
            )
            results.append((label, auto_labels, args))

        else:
            assert isinstance(args, GroupedBenchmark)

            # A later PR will populate model_path.
            model_path: Optional[str] = None

            for auto_labels, timer_args in args.flatten(model_path):
                results.append((label, auto_labels, timer_args))

    return tuple(results)


_TEMPDIR: Optional[str] = None
def get_temp_dir() -> str:
    global _TEMPDIR
    if _TEMPDIR is None:
        temp_dir = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, path=temp_dir)
        _TEMPDIR = temp_dir
    return _TEMPDIR
