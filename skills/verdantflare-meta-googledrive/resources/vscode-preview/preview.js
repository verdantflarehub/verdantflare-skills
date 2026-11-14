(() => {
  function hint(ev) {
    const el = ev.target;
    if (!el || !el.getAttribute || el.getAttribute('data-gdrive-preview') !== '1' || document.getElementById('gdrive-preview-security-hint')) return;
    const bar = document.createElement('div');
    bar.id = 'gdrive-preview-security-hint';
    bar.setAttribute('role', 'status');
    bar.style.cssText = 'padding:12px;border:1px solid currentColor;margin:8px 0';
    bar.textContent = 'GDrive 媒体未加载：请先确认映射盘和文件可读。若被安全策略拦截，运行命令 GDrive Preview: Configure Local Media Security，选择 Allow insecure local content 后刷新预览。';
    document.body.prepend(bar);
  }
  window.addEventListener('error', hint, true);
})();
