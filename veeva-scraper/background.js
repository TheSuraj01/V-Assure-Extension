chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type !== 'BADGE') return;
  const text = msg.count > 0 ? String(msg.count) : '';
  const color = msg.recording ? '#22d3ee' : '#64748b';
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
});
