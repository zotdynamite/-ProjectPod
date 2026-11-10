(() => {
  const form = document.getElementById('submit-show');
  const button = document.getElementById('fill-feed');
  const status = document.getElementById('feed-status');
  const preview = document.getElementById('feed-preview');
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  };
  const safeUrl = value => {
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
    catch (_) { return ''; }
  };
  const publication = value => {
    if (!value) return '';
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('en-GB', {
      timeZone: 'Asia/Kuala_Lumpur', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    }) + ' MYT';
  };
  const duration = value => {
    if (!/^\d+$/.test(String(value || ''))) return value || '';
    const seconds = Number(value);
    const minutes = Math.floor(seconds / 60);
    return seconds >= 3600 ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}` : `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
  };
  function render(data) {
    preview.replaceChildren();
    const head = node('div', 'feed-preview-head');
    if (safeUrl(data.cover)) {
      const image = node('img', 'feed-cover'); image.src = safeUrl(data.cover); image.alt = 'Podcast cover'; head.append(image);
    }
    const info = node('div');
    info.append(node('p', 'eyebrow', 'Feed preview'), node('h2', '', data.name),
      node('p', 'muted', `${data.episode_count} episodes in this feed`));
    head.append(info); preview.append(head);
    const latest = data.latest || {};
    if (safeUrl(latest.audio)) {
      const panel = node('section', 'episode-panel'); panel.setAttribute('aria-label', 'Latest episode');
      panel.append(node('p', 'eyebrow', 'Latest episode'), node('h2', '', latest.title || 'Latest episode'),
        node('p', 'episode-meta', [publication(latest.published), duration(latest.duration)].filter(Boolean).join(' · ')),
        node('p', 'episode-description', latest.description));
      const player = node('div', 'player');
      player.dataset.sting = '/static/podcast_stream_audio_logo.mp3';
      player.dataset.audio = safeUrl(latest.audio);
      player.dataset.title = latest.title || 'Latest episode';
      const play = node('button', 'play'); play.type = 'button';
      play.setAttribute('aria-label', 'Play ' + player.dataset.title);
      play.append(node('span', 'play-icon'));
      const copy = node('div', 'player-copy');
      copy.append(node('strong', 'player-title', player.dataset.title), node('span', 'player-time', '0:00 / –:–'));
      const wave = node('div', 'wave');
      wave.tabIndex = 0; wave.setAttribute('role', 'slider'); wave.setAttribute('aria-label', 'Playback progress');
      wave.setAttribute('aria-valuemin', '0'); wave.setAttribute('aria-valuemax', '100'); wave.setAttribute('aria-valuenow', '0');
      let seed = player.dataset.audio.length, bars = '';
      for (let i = 0; i < 48; i++) {
        seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
        bars += `<i style="height:${22 + (seed + i * 17) % 78}%"></i>`;
      }
      const dim = node('div', 'bars bars-dim'); dim.innerHTML = bars;
      const hot = node('div', 'bars bars-hot'); hot.innerHTML = bars;
      wave.append(dim, hot, node('span', 'scrub'));
      const live = node('p', 'player-live sr'); live.setAttribute('aria-live', 'polite');
      const hint = node('p', 'player-hint', 'First play starts with a short intro, then the episode.');
      player.append(play, copy, wave, live);
      panel.append(player, hint); preview.append(panel);
    }
    preview.hidden = false;
  }
  button.addEventListener('click', async () => {
    const rss = form.elements.rss.value.trim();
    preview.hidden = true;
    if (!safeUrl(rss)) {
      status.textContent = 'Enter a valid http(s) RSS feed URL first.'; status.className = 'feed-status error'; return;
    }
    button.disabled = true; status.className = 'feed-status'; status.textContent = 'Reading feed…';
    try {
      const response = await fetch('/api/preview-rss?url=' + encodeURIComponent(rss));
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error('feed');
      for (const key of ['name', 'description', 'cover', 'website', 'creator']) form.elements[key].value = data[key] || '';
      status.textContent = 'Filled from the feed — you can edit anything before submitting.' +
        (Number.isInteger(data.episode_count) ? ` ${data.episode_count} episodes found.` : '');
      status.className = 'feed-status success'; render(data);
    } catch (_) {
      status.className = 'feed-status error';
      status.textContent = 'Could not read that feed. Check the RSS URL or fill in the details yourself.';
    } finally { button.disabled = false; }
  });
})();
