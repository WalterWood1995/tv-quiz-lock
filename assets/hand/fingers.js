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
  if (typeof module !== "undefined") module.exports = countFingers;
  else root.countFingers = countFingers;
})(this);
