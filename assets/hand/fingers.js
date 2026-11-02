// 从 21 个手部关键点数伸出的手指（0–5）。关键点编号见 MediaPipe Hand Landmarker
(function (root) {
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function countFingers(p) {
    var n = 0;
    // 食指到小指：指尖比第二关节离手腕更远才算伸直，不依赖手的朝向
    [[8, 6], [12, 10], [16, 14], [20, 18]].forEach(function (f) {
      if (dist(p[f[0]], p[0]) > dist(p[f[1]], p[0]) * 1.1) n++;
    });
    // 拇指：指尖要明显离开小指根部，也要离开食指根部
    if (dist(p[4], p[17]) > dist(p[3], p[17]) * 1.05 && dist(p[4], p[5]) > dist(p[5], p[9]) * 0.9) n++;
    return n;
  }
  // 手势码：11 上 12 下 13 左 14 右（只伸食指，看它指的方向），5 张开手掌（确定），0 其他
  function readGesture(p) {
    var bent = [[12, 10], [16, 14], [20, 18]].every(function (f) { return dist(p[f[0]], p[0]) <= dist(p[f[1]], p[0]) * 1.1; });
    if (bent && dist(p[8], p[0]) > dist(p[6], p[0]) * 1.1) {
      // 前置摄像头原始画面左右相反：孩子往自己右边指，x 变小
      var dx = p[5].x - p[8].x, dy = p[8].y - p[5].y;
      if (Math.abs(dx) > Math.abs(dy)) return dx > 0 ? 14 : 13;
      return dy < 0 ? 11 : 12;
    }
    return countFingers(p) === 5 ? 5 : 0;
  }
  countFingers.readGesture = readGesture;
  if (typeof module !== "undefined") module.exports = countFingers;
  else root.readGesture = readGesture;
})(this);
