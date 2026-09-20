"""Phase 6.A: model-selection logic. The pure recommend_models() core is tested against
synthetic hardware snapshots (no GPU needed); write_env_models() is tested against tmp files."""

from tau_core.hardware import (
    CPU_FALLBACK_MODEL,
    Hardware,
    ModelRecommendation,
    main,
    recommend_models,
    write_env_models,
    write_env_values,
)


def hw(vram_mb: int, ram_mb: int = 32000, cores: int = 16, per_device: tuple[int, ...] = ()) -> Hardware:
    """Single-GPU-shaped by default (per_device empty, gpu_count 0) - matches every pre-Phase-10.5
    test below, which cares only about the combined `vram_mb` total. Pass `per_device` explicitly
    to build a genuinely multi-GPU snapshot."""
    return Hardware(
        gpu_vram_mb=vram_mb,
        system_ram_mb=ram_mb,
        cpu_cores=cores,
        gpu_visible_to_docker=None,
        gpu_count=len(per_device),
        gpu_vram_mb_per_device=per_device,
    )


def test_8gb_3070_picks_7b():
    """The live-bring-up case: an 8192 MB 3070 must pick the 7B, not the 14B that spills."""
    rec = recommend_models(hw(8192))
    assert rec.main == "qwen2.5:7b-instruct"
    assert rec.router == rec.main  # router == main, deliberately
    assert "8192" in rec.reason


def test_16gb_picks_14b():
    assert recommend_models(hw(16000)).main == "qwen2.5:14b-instruct"


def test_24gb_picks_32b():
    assert recommend_models(hw(24576)).main == "qwen2.5:32b-instruct"


def test_6gb_picks_3b():
    assert recommend_models(hw(6144)).main == "qwen2.5:3b-instruct"


def test_no_gpu_falls_back_to_cpu_model():
    rec = recommend_models(hw(0))
    assert rec.main == CPU_FALLBACK_MODEL
    assert "No NVIDIA GPU" in rec.reason


def test_tiny_gpu_below_smallest_tier_falls_back():
    rec = recommend_models(hw(2048))
    assert rec.main == CPU_FALLBACK_MODEL
    assert "below the smallest GPU tier" in rec.reason


def test_never_recommends_a_model_bigger_than_vram():
    """Core safety property: the chosen main model's tier threshold never exceeds available VRAM."""
    tier_min = {
        "qwen2.5:32b-instruct": 24000,
        "qwen2.5:14b-instruct": 12000,
        "qwen2.5:7b-instruct": 7000,
        "qwen2.5:3b-instruct": 0,  # fallback / smallest, always fits
    }
    for vram in (0, 2048, 6144, 8192, 12000, 16000, 24576, 48000):
        rec = recommend_models(hw(vram))
        assert tier_min[rec.main] <= vram, (vram, rec.main)


def test_multi_gpu_tiers_on_combined_vram_not_the_biggest_card():
    """Two 12 GB cards (24 GB combined) must reach the 32B tier - the same box read as a single
    12 GB card would only clear the 14B tier. This is the Phase 10.5 property: Ollama splits a
    model's layers across every GPU it can see, so combined VRAM is what actually bounds what
    loads, not the single biggest card."""
    two_cards = hw(24576, per_device=(12288, 12288))
    assert two_cards.gpu_count == 2
    rec = recommend_models(two_cards)
    assert rec.main == "qwen2.5:32b-instruct"


def test_multi_gpu_reason_mentions_the_gpu_count_and_breakdown():
    rec = recommend_models(hw(16384, per_device=(8192, 8192)))
    assert "2 GPUs" in rec.reason
    assert "16384" in rec.reason
    assert "8192" in rec.reason


def test_single_gpu_reason_does_not_mention_a_gpu_count():
    """A one-GPU box's reason should read like the pre-10.5 wording - no '1 GPUs' oddity."""
    rec = recommend_models(hw(8192))
    assert "GPUs" not in rec.reason


def test_probe_nvidia_gpus_mb_parses_multiple_lines(monkeypatch):
    """nvidia-smi's CSV output has one line per GPU; the probe must read all of them, largest
    first, not just the first line (which the old max()-based implementation also handled, but
    silently discarded everything except the single largest value)."""
    import subprocess as subprocess_module

    from tau_core import hardware as hardware_module

    class _FakeCompletedProcess:
        returncode = 0
        stdout = "8192\n24576\n12288\n"

    monkeypatch.setattr(hardware_module.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(
        subprocess_module, "run", lambda *a, **k: _FakeCompletedProcess()
    )
    monkeypatch.setattr(hardware_module, "subprocess", subprocess_module)

    per_device = hardware_module._probe_nvidia_gpus_mb()
    assert per_device == (24576, 12288, 8192)


def test_probe_hardware_sums_per_device_into_gpu_vram_mb(monkeypatch):
    from tau_core import hardware as hardware_module

    monkeypatch.setattr(hardware_module, "_probe_nvidia_gpus_mb", lambda: (12288, 12288))
    monkeypatch.setattr(hardware_module, "_probe_ram_mb", lambda: 65536)

    result = hardware_module.probe_hardware()
    assert result.gpu_count == 2
    assert result.gpu_vram_mb_per_device == (12288, 12288)
    assert result.gpu_vram_mb == 24576  # the combined total, not either individual card


def test_write_env_models_preserves_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "OLLAMA_HOST=http://localhost:11434\n"
        "OLLAMA_MODEL=qwen2.5:14b-instruct\n"
        "OLLAMA_ROUTER_MODEL=qwen2.5:14b-instruct\n"
        "# a comment\n"
        "TAU_ROUTING_THRESHOLD=0.5\n",
        encoding="utf-8",
    )
    write_env_models(env, ModelRecommendation("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", "why"))
    text = env.read_text(encoding="utf-8")
    assert "OLLAMA_MODEL=qwen2.5:7b-instruct" in text
    assert "OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct" in text
    # Untouched lines survive.
    assert "OLLAMA_HOST=http://localhost:11434" in text
    assert "# a comment" in text
    assert "TAU_ROUTING_THRESHOLD=0.5" in text
    # No duplicate keys introduced.
    assert text.count("OLLAMA_MODEL=") == 1
    assert text.count("OLLAMA_ROUTER_MODEL=") == 1


def test_write_env_models_appends_missing_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=http://localhost:11434\n", encoding="utf-8")
    write_env_models(env, ModelRecommendation("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", "why"))
    text = env.read_text(encoding="utf-8")
    assert "OLLAMA_MODEL=qwen2.5:7b-instruct" in text
    assert "OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct" in text
    assert "OLLAMA_HOST=http://localhost:11434" in text


def test_write_env_models_creates_file_when_absent(tmp_path):
    env = tmp_path / ".env"
    write_env_models(env, ModelRecommendation("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", "why"))
    assert env.is_file()
    assert "OLLAMA_MODEL=qwen2.5:7b-instruct" in env.read_text(encoding="utf-8")


def test_write_env_values_is_the_generic_form_write_env_models_delegates_to(tmp_path):
    """write_env_models is now a thin wrapper - prove the underlying primitive does the same
    in-place, non-destructive edit directly, since `tau model set` (the CLI's --set-model) calls
    it with an arbitrary single key rather than a full ModelRecommendation."""
    env = tmp_path / ".env"
    env.write_text("OLLAMA_HOST=http://localhost:11434\nOLLAMA_MODEL=old\n", encoding="utf-8")
    write_env_values(env, {"OLLAMA_MODEL": "qwen2.5:3b-instruct"})
    text = env.read_text(encoding="utf-8")
    assert "OLLAMA_MODEL=qwen2.5:3b-instruct" in text
    assert "OLLAMA_HOST=http://localhost:11434" in text
    # OLLAMA_ROUTER_MODEL was never in the updates dict, so it must not appear.
    assert "OLLAMA_ROUTER_MODEL" not in text


def test_cli_set_model_writes_only_ollama_model(tmp_path):
    """--set-model bypasses the hardware probe entirely and never touches
    OLLAMA_ROUTER_MODEL - a manual override shouldn't silently change the router pick too."""
    env = tmp_path / ".env"
    env.write_text("OLLAMA_ROUTER_MODEL=qwen2.5:3b-instruct\n", encoding="utf-8")
    exit_code = main(["--set-model", "qwen2.5:32b-instruct", "--env", str(env)])
    assert exit_code == 0
    text = env.read_text(encoding="utf-8")
    assert "OLLAMA_MODEL=qwen2.5:32b-instruct" in text
    # Untouched - a manual model override must not silently change the router pick too.
    assert "OLLAMA_ROUTER_MODEL=qwen2.5:3b-instruct" in text
