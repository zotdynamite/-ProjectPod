/* Custom episode player. The approved intro sting plays once on first play, then the episode. */
(() => {
  const states = new WeakMap();

  function fmt(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "–:–";
    const whole = Math.floor(seconds);
    const h = Math.floor(whole / 3600);
    const m = Math.floor((whole % 3600) / 60);
    const s = whole % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }

  function setIcon(state, playing) {
    state.player.classList.toggle("is-playing", playing);
    state.btn.setAttribute("aria-label", playing ? "Pause" : "Play " + (state.player.dataset.title || "episode"));
  }

  function say(state, text) {
    if (state.live) state.live.textContent = text;
  }

  function fail(state, message) {
    state.mode = "idle";
    setIcon(state, false);
    say(state, message);
    if (state.time) state.time.textContent = message;
  }

  function setProgress(state, ratio) {
    const amount = Math.max(0, Math.min(1, ratio || 0));
    if (state.hot) state.hot.style.clipPath = `inset(0 ${(1 - amount) * 100}% 0 0)`;
    if (state.scrub) state.scrub.style.left = `${amount * 100}%`;
    if (state.wave) state.wave.setAttribute("aria-valuenow", String(Math.round(amount * 100)));
  }

  function render(state) {
    const episode = state.episode;
    const duration = Number.isFinite(episode.duration) ? episode.duration : NaN;
    if (state.mode === "sting") {
      if (state.time) state.time.textContent = `Intro ${fmt(state.sting.currentTime)}`;
      setProgress(state, 0);
      return;
    }
    const current = episode.currentTime || 0;
    if (state.time) state.time.textContent = `${fmt(current)} / ${fmt(duration)}`;
    setProgress(state, duration ? current / duration : 0);
  }

  function playEpisode(state) {
    state.mode = "episode";
    setIcon(state, true);
    if (state.episode.ended) state.episode.currentTime = 0;
    return state.episode.play().catch(() => fail(state, "This episode could not be played."));
  }

  function toggle(state) {
    if (state.mode === "sting") {
      state.sting.pause();
      state.mode = "paused";
      setIcon(state, false);
      return;
    }
    if (state.mode === "episode" && !state.episode.paused) {
      state.episode.pause();
      state.mode = "paused";
      setIcon(state, false);
      return;
    }
    if (!state.stingDone) {
      state.mode = "sting";
      setIcon(state, true);
      say(state, "Playing intro");
      state.sting.play().catch(() => {
        if (state.stingDone) return;
        state.mode = "idle";
        setIcon(state, false);
        say(state, "Press play to start.");
      });
      return;
    }
    playEpisode(state);
  }

  function seek(state, event) {
    if (!state.stingDone || !state.episode.duration) {
      if (state.mode !== "sting") toggle(state);
      return;
    }
    const rect = state.wave.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    state.episode.currentTime = ratio * state.episode.duration;
    setProgress(state, ratio);
    if (state.episode.paused) playEpisode(state);
  }

  function prepare(player) {
    if (!player || !player.dataset.audio || states.has(player)) return;
    const sting = new Audio(player.dataset.sting);
    const episode = new Audio(player.dataset.audio);
    sting.preload = "auto";
    episode.preload = "metadata";
    const state = {
      player,
      sting,
      episode,
      stingDone: false,
      mode: "idle",
      btn: player.querySelector(".play"),
      time: player.querySelector(".player-time"),
      hot: player.querySelector(".bars-hot"),
      scrub: player.querySelector(".scrub"),
      wave: player.querySelector(".wave"),
      live: player.querySelector(".player-live"),
    };
    states.set(player, state);
    episode.addEventListener("loadedmetadata", () => render(state));
    episode.addEventListener("timeupdate", () => render(state));
    episode.addEventListener("ended", () => {
      state.mode = "idle";
      setIcon(state, false);
      render(state);
    });
    episode.addEventListener("error", () => fail(state, "This episode could not be played."));
    sting.addEventListener("timeupdate", () => {
      if (state.mode === "sting") render(state);
    });
    sting.addEventListener("ended", () => {
      state.stingDone = true;
      state.mode = "episode";
      say(state, "Intro finished");
      episode.play().catch(() => fail(state, "Press play to start the episode."));
      setIcon(state, true);
    });
    sting.addEventListener("error", () => {
      state.stingDone = true;
      if (state.mode === "sting") playEpisode(state);
    });
    if (state.wave) {
      state.wave.addEventListener("click", (event) => seek(state, event));
      state.wave.addEventListener("keydown", (event) => {
        if (!state.stingDone || !episode.duration) return;
        if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
        event.preventDefault();
        const delta = event.key === "ArrowRight" ? 5 : -5;
        episode.currentTime = Math.min(episode.duration, Math.max(0, (episode.currentTime || 0) + delta));
        render(state);
      });
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest(".player .play");
    if (!button || button.disabled) return;
    const player = button.closest(".player");
    if (!player || !player.dataset.audio) return;
    prepare(player);
    toggle(states.get(player));
  });

  function scan() {
    document.querySelectorAll(".player[data-audio]").forEach(prepare);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });

  const share = document.getElementById("share-show");
  if (share) {
    share.addEventListener("click", async () => {
      const url = location.href;
      const title = share.dataset.title || document.title;
      const label = share.querySelector(".share-label");
      if (navigator.share) {
        try {
          await navigator.share({ title, url });
          return;
        } catch (err) {
          if (err && err.name === "AbortError") return;
        }
      }
      try {
        await navigator.clipboard.writeText(url);
      } catch (_) {
        const input = document.createElement("input");
        input.value = url;
        document.body.append(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      if (label) {
        const original = label.textContent;
        label.textContent = "Link copied";
        window.setTimeout(() => { label.textContent = original; }, 1800);
      }
    });
  }
})();
