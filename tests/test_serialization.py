from pathlib import Path
from shutil import rmtree

import lightning
import numpy as np
import torch

from kooplearn.data import traj_to_contexts
from kooplearn.datasets import Mock
from kooplearn.nn.data import TrajToContextsDataset

NUM_SAMPLES = 100
DIM = 7

TRAJ = Mock(num_features=DIM, rng_seed=0).sample(None, NUM_SAMPLES).astype(np.float32)
DATA = traj_to_contexts(TRAJ)
NN_DATA = TrajToContextsDataset(TRAJ)
NN_DATALOADER = torch.utils.data.DataLoader(NN_DATA, batch_size=10, shuffle=True)


def _make_tmp_path(model_name: str):
    tmp_path = Path(__file__).parent / f"tmp/{model_name}.bin"
    return tmp_path


def _cleanup():
    tmp_path = Path(__file__).parent / "tmp"
    rmtree(tmp_path)


def _allclose(a, b):
    return np.allclose(a, b, rtol=1e-3, atol=1e-5)


class Encoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = torch.nn.Linear(DIM, 32)

    def forward(self, x):
        return self.encoder(x)


def test_KernelDMD_serialization():
    from kooplearn.models import KernelDMD

    model = KernelDMD().fit(DATA)
    tmp_path = _make_tmp_path(model.__class__.__name__)
    model.save(tmp_path)
    restored_model = KernelDMD.load(tmp_path)

    # Check that predict, eig and modes return the same values
    assert _allclose(
        model.predict(DATA[:, : model.lookback_len, ...]),
        restored_model.predict(DATA[:, : model.lookback_len, ...]),
    )

    assert _allclose(
        model.modes(DATA[:, : model.lookback_len, ...]),
        restored_model.modes(DATA[:, : model.lookback_len, ...]),
    )

    e1, l1, r1 = model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )
    e2, l2, r2 = restored_model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )

    assert _allclose(e1, e2)
    assert _allclose(l1, l2)
    assert _allclose(r1, r2)

    _cleanup()


def test_ExtendedDMD_serialization():
    from kooplearn.models import ExtendedDMD

    model = ExtendedDMD().fit(DATA)
    tmp_path = _make_tmp_path(model.__class__.__name__)
    model.save(tmp_path)
    restored_model = ExtendedDMD.load(tmp_path)

    # Check that predict, eig and modes return the same values
    assert _allclose(
        model.predict(DATA[:, : model.lookback_len, ...]),
        restored_model.predict(DATA[:, : model.lookback_len, ...]),
    )

    assert _allclose(
        model.modes(DATA[:, : model.lookback_len, ...]),
        restored_model.modes(DATA[:, : model.lookback_len, ...]),
    )

    e1, l1, r1 = model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )
    e2, l2, r2 = restored_model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )

    assert _allclose(e1, e2)
    assert _allclose(l1, l2)
    assert _allclose(r1, r2)

    _cleanup()


def test_DPNet_serialization():
    from kooplearn.models import DeepEDMD
    from kooplearn.models.feature_maps import DPNet

    trainer = lightning.Trainer(
        enable_progress_bar=False,
        enable_checkpointing=False,
        enable_model_summary=False,
        accelerator="cpu",
        max_epochs=1,
    )
    feature_map = DPNet(
        Encoder, torch.optim.SGD, trainer, optimizer_kwargs={"lr": 1e-6}
    )
    feature_map.fit(NN_DATALOADER)

    tmp_path = _make_tmp_path(feature_map.__class__.__name__)
    feature_map.save(tmp_path)
    restored_feature_map = DPNet.load(tmp_path)

    assert _allclose(feature_map(TRAJ), restored_feature_map(TRAJ))

    model = DeepEDMD(feature_map=feature_map).fit(DATA)
    tmp_path = _make_tmp_path(model.__class__.__name__)
    model.save(tmp_path)
    restored_model = DeepEDMD.load(tmp_path)

    # Check that predict, eig and modes return the same values
    assert _allclose(
        model.predict(DATA[:, : model.lookback_len, ...]),
        restored_model.predict(DATA[:, : model.lookback_len, ...]),
    )

    assert _allclose(
        model.modes(DATA[:, : model.lookback_len, ...]),
        restored_model.modes(DATA[:, : model.lookback_len, ...]),
    )

    e1, l1, r1 = model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )
    e2, l2, r2 = restored_model.eig(
        eval_left_on=DATA[:, : model.lookback_len, ...],
        eval_right_on=DATA[:, : model.lookback_len, ...],
    )

    assert _allclose(e1, e2)
    assert _allclose(l1, l2)
    assert _allclose(r1, r2)

    _cleanup()
