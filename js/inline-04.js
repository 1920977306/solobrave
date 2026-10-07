/* ===== index.html 内联块 4 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: function jumpToTokens() { */
function jumpToTokens() {
  openTokenStats();
}
function openCompressModal() {
  document.getElementById('compressModal').classList.add('show');
}
function closeCompressModal() {
  document.getElementById('compressModal').classList.remove('show');
}
function doCompress() {
  closeCompressModal();
  showToast('❄️ 上下文压缩完成');
}
function openScanModal() {
  document.getElementById('scanModal').classList.add('show');
}
function closeScanModal() {
  document.getElementById('scanModal').classList.remove('show');
}
function doScan() {
  closeScanModal();
  showToast('🔍 上下文扫描完成');
}
function openResetModal() {
  document.getElementById('resetModal').classList.add('show');
}
function closeResetModal() {
  document.getElementById('resetModal').classList.remove('show');
}
function doReset() {
  closeResetModal();
  // 统一调用 resetContext，真正清空后端聊天记录 + OpenClaw session
  resetContext();
}