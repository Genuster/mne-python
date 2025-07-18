# Authors: The MNE-Python contributors.
# License: BSD-3-Clause
# Copyright the MNE-Python contributors.

import sys
from pathlib import Path

import numpy as np
import pytest
from numpy.testing import assert_array_almost_equal, assert_array_equal

pytest.importorskip("sklearn")

from sklearn.pipeline import Pipeline
from sklearn.utils.estimator_checks import parametrize_with_checks

from mne import Epochs, create_info, io, pick_types, read_events
from mne._fiff.pick import _picks_to_idx
from mne.decoding import CSP
from mne.decoding._mod_ged import _get_spectral_ratio
from mne.decoding.ssd import SSD
from mne.filter import filter_data
from mne.time_frequency import psd_array_welch

freqs_sig = 9, 12
freqs_noise = 8, 13

data_dir = Path(__file__).parents[2] / "io" / "tests" / "data"
raw_fname = data_dir / "test_raw.fif"
event_name = data_dir / "test-eve.fif"
tmin, tmax = -0.1, 0.2
event_id = dict(aud_l=1, vis_l=3)
start, stop = 0, 8


def simulate_data(
    freqs_sig=(9, 12),
    n_trials=100,
    n_channels=20,
    n_samples=500,
    samples_per_second=250,
    n_components=5,
    SNR=0.05,
    random_state=42,
):
    """Simulate data according to an instantaneous mixin model.

    Data are simulated in the statistical source space, where n=n_components
    sources contain the peak of interest.
    """
    rng = np.random.RandomState(random_state)

    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
        fir_design="firwin",
    )

    # generate an orthogonal mixin matrix
    mixing_mat = np.linalg.svd(rng.randn(n_channels, n_channels))[0]
    # define sources
    S_s = rng.randn(n_trials * n_samples, n_components)
    # filter source in the specific freq. band of interest
    S_s = filter_data(S_s.T, samples_per_second, **filt_params_signal).T
    S_n = rng.randn(n_trials * n_samples, n_channels - n_components)
    S = np.hstack((S_s, S_n))
    # mix data
    X_s = np.dot(mixing_mat[:, :n_components], S_s.T).T
    X_n = np.dot(mixing_mat[:, n_components:], S_n.T).T
    # add noise
    X_s = X_s / np.linalg.norm(X_s, "fro")
    X_n = X_n / np.linalg.norm(X_n, "fro")
    X = SNR * X_s + (1 - SNR) * X_n
    X = X.T
    S = S.T
    return X, mixing_mat, S


@pytest.mark.slowtest
def test_ssd():
    """Test Common Spatial Patterns algorithm on raw data."""
    X, A, S = simulate_data()
    sf = 250
    n_channels = X.shape[0]
    info = create_info(ch_names=n_channels, sfreq=sf, ch_types="eeg")
    n_components_true = 5

    # Init
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    # freq no int
    freq = "foo"
    filt_params_signal = dict(
        l_freq=freq, h_freq=freqs_sig[1], l_trans_bandwidth=1, h_trans_bandwidth=1
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    with pytest.raises(TypeError, match="must be an instance "):
        ssd.fit(X)

    # Wrongly specified noise band
    freq = 2
    filt_params_signal = dict(
        l_freq=freq, h_freq=freqs_sig[1], l_trans_bandwidth=1, h_trans_bandwidth=1
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    with pytest.raises(ValueError, match="Wrongly specified "):
        ssd.fit(X)

    # filt param no dict
    filt_params_signal = freqs_sig
    filt_params_noise = freqs_noise
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    with pytest.raises(ValueError, match="must be defined"):
        ssd.fit(X)

    # Data type
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    raw = io.RawArray(X, info)

    with pytest.raises(ValueError):
        ssd.fit(raw)

    # check non-boolean return_filtered
    ssd = SSD(info, filt_params_signal, filt_params_noise, return_filtered=0)
    with pytest.raises(TypeError, match="return_filtered"):
        ssd.fit(X)

    # check non-boolean sort_by_spectral_ratio
    ssd = SSD(info, filt_params_signal, filt_params_noise, sort_by_spectral_ratio=0)
    with pytest.raises(TypeError, match="sort_by_spectral_ratio"):
        ssd.fit(X)

    # More than 1 channel type
    ch_types = np.reshape([["mag"] * 10, ["eeg"] * 10], n_channels)
    info_2 = create_info(ch_names=n_channels, sfreq=sf, ch_types=ch_types)

    ssd = SSD(info_2, filt_params_signal, filt_params_noise)
    with pytest.raises(ValueError, match="At this point SSD"):
        ssd.fit(X)

    # Number of channels
    info_3 = create_info(ch_names=n_channels + 1, sfreq=sf, ch_types="eeg")
    ssd = SSD(info_3, filt_params_signal, filt_params_noise)
    with pytest.raises(ValueError, match="channels but expected"):
        ssd.fit(X)

    # Fit
    n_components = 10
    ssd = SSD(info, filt_params_signal, filt_params_noise, n_components=n_components)

    # Call transform before fit
    pytest.raises(AttributeError, ssd.transform, X)

    # Check outputs
    ssd.fit(X)

    assert ssd.filters_.shape == (n_channels, n_channels)
    assert ssd.patterns_.shape == (n_channels, n_channels)

    # Transform
    X_ssd = ssd.fit_transform(X)
    assert X_ssd.shape[0] == n_components
    # back and forward
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(X)
    X_denoised = ssd.apply(X)
    assert_array_almost_equal(X_denoised, X)
    # denoised by low-rank-factorization
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=n_components,
        sort_by_spectral_ratio=True,
    )
    ssd.fit(X)
    X_denoised = ssd.apply(X)
    assert np.linalg.matrix_rank(X_denoised) == n_components

    # Power ratio ordering
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(X)
    spec_ratio, sorter_spec = _get_spectral_ratio(
        ssd.transform(X), ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )
    # since we now that the number of true components is 5, the relative
    # difference should be low for the first 5 components and then increases
    index_diff = np.argmax(-np.diff(spec_ratio))
    assert index_diff == n_components_true - 1
    # Check detected peaks
    # fit ssd
    n_components = n_components_true
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=n_components,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(X)

    out = ssd.transform(X)
    psd_out, _ = psd_array_welch(out[0], sfreq=250, n_fft=250)
    psd_S, _ = psd_array_welch(S[0], sfreq=250, n_fft=250)
    corr = np.abs(np.corrcoef((psd_out, psd_S))[0, 1])
    assert np.abs(corr) > 0.95
    # Check pattern estimation
    # Since there is no exact ordering of the recovered patterns
    # a pair-wise greedy search will be done
    error = list()
    for ii in range(n_channels):
        corr = np.abs(np.corrcoef(ssd.patterns_[ii, :].T, A[:, 0])[0, 1])
        error.append(1 - corr)
        min_err = np.min(error)
    assert min_err < 0.3  # threshold taken from SSD original paper


def test_ssd_epoched_data():
    """Test Common Spatial Patterns algorithm on epoched data.

    Compare the outputs when raw data is used.
    """
    X, A, S = simulate_data(n_trials=100, n_channels=20, n_samples=500)
    sf = 250
    n_channels = X.shape[0]
    info = create_info(ch_names=n_channels, sfreq=sf, ch_types="eeg")
    n_components_true = 5

    # Build epochs as sliding windows over the continuous raw file

    # Epoch length is 1 second
    X_e = np.reshape(X, (100, 20, 500))

    # Fit
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )

    # ssd on epochs
    ssd_e = SSD(info, filt_params_signal, filt_params_noise)
    ssd_e.fit(X_e)
    # ssd on raw
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    ssd.fit(X)

    # Check if the 5 first 5 components are the same for both
    _, sorter_spec_e = _get_spectral_ratio(
        ssd_e.transform(X_e),
        ssd_e.sfreq_,
        ssd_e.n_fft_,
        ssd_e.freqs_signal_,
        ssd_e.freqs_noise_,
    )
    _, sorter_spec = _get_spectral_ratio(
        ssd.transform(X), ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )
    assert_array_equal(
        sorter_spec_e[:n_components_true], sorter_spec[:n_components_true]
    )


def test_ssd_pipeline():
    """Test if SSD works in a pipeline."""
    sf = 250
    X, A, S = simulate_data(n_trials=100, n_channels=20, n_samples=500)
    X_e = np.reshape(X, (100, 20, 500))
    # define bynary random output
    y = np.random.RandomState(0).randint(2, size=100)

    info = create_info(ch_names=20, sfreq=sf, ch_types="eeg")

    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )
    ssd = SSD(info, filt_params_signal, filt_params_noise)
    csp = CSP()
    pipe = Pipeline([("SSD", ssd), ("CSP", csp)])
    pipe.set_params(SSD__n_components=5)
    pipe.set_params(CSP__n_components=2)
    out = pipe.fit_transform(X_e, y)
    assert out.shape == (100, 2)
    assert pipe.get_params()["SSD__n_components"] == 5


def test_sorting():
    """Test sorting learning during training."""
    X, _, _ = simulate_data(n_trials=100, n_channels=20, n_samples=500)
    # Epoch length is 1 second
    X = np.reshape(X, (100, 20, 500))
    # split data
    Xtr, Xte = X[:80], X[80:]
    sf = 250
    n_channels = Xtr.shape[1]
    info = create_info(ch_names=n_channels, sfreq=sf, ch_types="eeg")

    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=4,
        h_trans_bandwidth=4,
    )

    # check sort_by_spectral_ratio set to False
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(Xtr)
    _, sorter_tr = _get_spectral_ratio(
        ssd.transform(Xtr), ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )
    _, sorter_te = _get_spectral_ratio(
        ssd.transform(Xte), ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )
    assert any(sorter_tr != sorter_te)

    # check sort_by_spectral_ratio set to True
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=True,
    )
    ssd.fit(Xtr)

    # check sorters
    sorter_in = ssd.sorter_
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(Xtr)
    _, sorter_out = _get_spectral_ratio(
        ssd.transform(Xtr), ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )

    assert all(sorter_in == sorter_out)


def test_return_filtered():
    """Test return filtered option."""
    # Check return_filtered
    # Simulated more noise data and with broader frequency than the desired
    X, _, _ = simulate_data(SNR=0.9, freqs_sig=[4, 13])
    sf = 250
    n_channels = X.shape[0]
    info = create_info(ch_names=n_channels, sfreq=sf, ch_types="eeg")

    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )

    # return filtered to true
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        sort_by_spectral_ratio=False,
        return_filtered=True,
    )
    ssd.fit(X)

    out = ssd.transform(X)
    psd_out, freqs = psd_array_welch(out[0], sfreq=250, n_fft=250)
    freqs_up = int(freqs[psd_out > 0.5][0]), int(freqs[psd_out > 0.5][-1])
    assert freqs_up == freqs_sig

    # return filtered to false
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        sort_by_spectral_ratio=False,
        return_filtered=False,
    )
    ssd.fit(X)

    out = ssd.transform(X)
    psd_out, freqs = psd_array_welch(out[0], sfreq=250, n_fft=250)
    freqs_up = int(freqs[psd_out > 0.5][0]), int(freqs[psd_out > 0.5][-1])
    assert freqs_up != freqs_sig


def test_non_full_rank_data():
    """Test that the method works with non-full rank data."""
    n_channels = 10
    X, _, _ = simulate_data(SNR=0.9, freqs_sig=[4, 13], n_channels=n_channels)
    info = create_info(ch_names=n_channels, sfreq=250, ch_types="eeg")

    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )

    # Make data non-full rank
    rank = 5
    X[rank:] = X[:rank]  # an extreme example, but a valid one
    assert np.linalg.matrix_rank(X) == rank

    ssd = SSD(info, filt_params_signal, filt_params_noise)
    if sys.platform == "darwin":
        pytest.xfail("Unknown linalg bug (Accelerate?)")
    ssd.fit(X)


def test_picks_arg():
    """Test that picks argument works as expected."""
    raw = io.read_raw_fif(raw_fname, preload=False)
    events = read_events(event_name)
    picks = pick_types(
        raw.info, meg=True, eeg=True, stim=False, ecg=False, eog=False, exclude="bads"
    )
    raw.add_proj([], remove_existing=True)
    epochs = Epochs(
        raw,
        events,
        event_id,
        -0.1,
        1,
        picks=picks,
        baseline=(None, 0),
        preload=True,
        proj=False,
    )
    X = epochs.get_data(copy=False)
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=3,
        h_trans_bandwidth=3,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=3,
        h_trans_bandwidth=3,
    )
    picks = ["eeg"]
    info = epochs.info
    picks_idx = _picks_to_idx(info, picks)

    # Test when return_filtered is False
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        picks=picks_idx,
        return_filtered=False,
    )
    ssd.fit(X).transform(X)

    # Test when return_filtered is true
    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        picks=picks_idx,
        return_filtered=True,
        n_fft=64,
    )
    ssd.fit(X).transform(X)


def test_get_spectral_ratio():
    """Test that method is the same as function in _mod_ged.py."""
    X, _, _ = simulate_data()
    sf = 250
    n_channels = X.shape[0]
    info = create_info(ch_names=n_channels, sfreq=sf, ch_types="eeg")

    # Init
    filt_params_signal = dict(
        l_freq=freqs_sig[0],
        h_freq=freqs_sig[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )
    filt_params_noise = dict(
        l_freq=freqs_noise[0],
        h_freq=freqs_noise[1],
        l_trans_bandwidth=1,
        h_trans_bandwidth=1,
    )

    ssd = SSD(
        info,
        filt_params_signal,
        filt_params_noise,
        n_components=None,
        sort_by_spectral_ratio=False,
    )
    ssd.fit(X)
    ssd_sources = ssd.transform(X)
    spec_ratio_ssd, sorter_spec_ssd = ssd.get_spectral_ratio(ssd_sources)
    spec_ratio_ged, sorter_spec_ged = _get_spectral_ratio(
        ssd_sources, ssd.sfreq_, ssd.n_fft_, ssd.freqs_signal_, ssd.freqs_noise_
    )
    assert_array_equal(spec_ratio_ssd, spec_ratio_ged)
    assert_array_equal(sorter_spec_ssd, sorter_spec_ged)


@pytest.mark.filterwarnings("ignore:.*invalid value encountered in divide.*")
@pytest.mark.filterwarnings("ignore:.*is longer than.*")
@parametrize_with_checks(
    [
        SSD(
            100.0,
            dict(l_freq=0.0, h_freq=30.0),
            dict(l_freq=0.0, h_freq=40.0),
        )
    ]
)
def test_sklearn_compliance(estimator, check):
    """Test LinearModel compliance with sklearn."""
    pytest.importorskip("sklearn", minversion="1.4")  # TODO VERSION remove on 1.4+
    ignores = (
        "check_methods_sample_order_invariance",
        # Shape stuff
        "check_fit_idempotent",
        "check_methods_subset_invariance",
        "check_transformer_general",
        "check_transformer_data_not_an_array",
    )
    if any(ignore in str(check) for ignore in ignores):
        return

    check(estimator)
