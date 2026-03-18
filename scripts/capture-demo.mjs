import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const imageOutputDir = path.join(projectRoot, "docs", "images");
const baseUrl = process.env.CAPTURE_BASE_URL || "http://127.0.0.1:5000";
const browserChannel = process.env.CAPTURE_BROWSER_CHANNEL || "msedge";
const pythonCommand = process.env.PYTHON || "python";
const ffmpegCommand = process.env.FFMPEG || "ffmpeg";
const gifOutputPath = path.join(imageOutputDir, "demo-flow.gif");

const scenarioShots = [
  { name: "overview", file: "01-overview.png" },
  { name: "recording", file: "02-recording-state.png" },
  { name: "answer", file: "03-answer-ready.png" },
  { name: "analysis-progress", file: "04-analysis-progress.png" },
  { name: "final", file: "05-final-dashboard.png" },
  { name: "recall", file: "06-recall-test.png" },
];

const cardShots = [
  {
    name: "status",
    scenario: "final",
    selector: "#statusCard",
    file: "card-status-risk.png",
  },
  {
    name: "metrics",
    scenario: "final",
    selector: "#sidebarMetricsDisclosure",
    file: "card-sidebar-metrics.png",
    setup: async (page) => openDisclosure(page, "#sidebarMetricsDisclosure"),
  },
  {
    name: "process",
    scenario: "analysis-progress",
    selector: "#processPanel",
    file: "card-process-panel.png",
  },
  {
    name: "session-summary",
    scenario: "final",
    selector: "#analysisHeroCard",
    file: "card-session-summary.png",
  },
  {
    name: "analysis-result",
    scenario: "final",
    selector: "#analysisResultCard",
    file: "card-analysis-result.png",
    setup: async (page) => openDisclosure(page, "#analysisDetailDisclosure"),
  },
  {
    name: "feature-breakdown",
    scenario: "final",
    selector: "#featureBreakdownCard",
    file: "card-feature-breakdown.png",
    setup: async (page) => openDisclosure(page, "#analysisDetailDisclosure"),
  },
  {
    name: "confidence",
    scenario: "final",
    selector: "#confidenceCard",
    file: "card-confidence.png",
    setup: async (page) => openDisclosure(page, "#analysisDetailDisclosure"),
  },
  {
    name: "timeline",
    scenario: "final",
    selector: "#historyDisclosure",
    file: "card-timeline-report.png",
    setup: async (page) => openDisclosure(page, "#historyDisclosure"),
  },
  {
    name: "recall",
    scenario: "recall",
    selector: "#recallDisclosure",
    file: "card-recall-test.png",
    setup: async (page) => openDisclosure(page, "#recallDisclosure"),
  },
];

async function isServerReady() {
  try {
    const response = await fetch(`${baseUrl}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForServer(timeoutMs = 60000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    if (await isServerReady()) {
      return true;
    }
    await delay(1000);
  }

  throw new Error(`서버가 ${baseUrl} 에서 준비되지 않았습니다.`);
}

function startServerProcess() {
  return spawn(pythonCommand, ["app.py"], {
    cwd: projectRoot,
    stdio: "ignore",
  });
}

async function ensureOutputDirectory() {
  await fs.mkdir(imageOutputDir, { recursive: true });
}

function runCommand(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: projectRoot,
      stdio: "ignore",
      ...options,
    });

    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }

      reject(new Error(`${command} 명령이 종료 코드 ${code}로 실패했습니다.`));
    });
  });
}

async function buildGif() {
  const manifestPath = path.join(imageOutputDir, "demo-flow.frames.txt");
  const manifestLines = [];

  for (const [index, scenario] of scenarioShots.entries()) {
    const framePath = path
      .join(imageOutputDir, scenario.file)
      .replace(/\\/g, "/");
    const duration = index === scenarioShots.length - 1 ? 1.8 : 1.25;

    manifestLines.push(`file '${framePath}'`);
    manifestLines.push(`duration ${duration}`);
  }

  const lastFramePath = path
    .join(imageOutputDir, scenarioShots[scenarioShots.length - 1].file)
    .replace(/\\/g, "/");
  manifestLines.push(`file '${lastFramePath}'`);

  await fs.writeFile(manifestPath, `${manifestLines.join("\n")}\n`, "utf8");

  try {
    await runCommand(ffmpegCommand, [
      "-y",
      "-f",
      "concat",
      "-safe",
      "0",
      "-i",
      manifestPath,
      "-filter_complex",
      "fps=8,scale=1400:-1:flags=lanczos,split[s0][s1];[s0]palettegen=reserve_transparent=0[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
      gifOutputPath,
    ]);
  } finally {
    await fs.rm(manifestPath, { force: true });
  }
}

async function prepareScenario(page, scenarioName) {
  const targetUrl =
    `${baseUrl}/?demo=${encodeURIComponent(scenarioName)}` + "&capture=docs";
  await page.goto(targetUrl, { waitUntil: "networkidle" });
  await page.waitForFunction(
    (expectedScenario) =>
      document.body?.dataset?.demoReady === "true" &&
      document.body?.dataset?.demoScenario === expectedScenario,
    scenarioName,
  );
  await page.waitForTimeout(500);
}

async function openDisclosure(page, selector) {
  await page.locator(selector).evaluate((element) => {
    element.open = true;
  });
  await page.waitForTimeout(300);
}

async function captureFullScreenshots(page) {
  for (const scenario of scenarioShots) {
    console.log(`[capture] ${scenario.name} 전체 화면을 캡처합니다.`);
    await prepareScenario(page, scenario.name);

    const outputPath = path.join(imageOutputDir, scenario.file);
    await page.screenshot({
      path: outputPath,
      fullPage: true,
    });

    console.log(`[capture] 저장 완료: ${outputPath}`);
  }
}

async function captureCardScreenshots(page) {
  for (const shot of cardShots) {
    console.log(`[capture] ${shot.name} 카드를 캡처합니다.`);
    await prepareScenario(page, shot.scenario);

    if (shot.setup) {
      await shot.setup(page);
    }

    const locator = page.locator(shot.selector);
    await locator.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);

    const outputPath = path.join(imageOutputDir, shot.file);
    await locator.screenshot({
      path: outputPath,
      animations: "disabled",
    });

    console.log(`[capture] 저장 완료: ${outputPath}`);
  }
}

async function captureScreenshots() {
  let serverProcess = null;

  if (!(await isServerReady())) {
    console.log("[capture] 로컬 서버를 시작합니다...");
    serverProcess = startServerProcess();
    await waitForServer();
  } else {
    console.log("[capture] 이미 실행 중인 서버를 사용합니다.");
  }

  const browser = await chromium.launch({
    headless: true,
    channel: browserChannel,
  });

  try {
    const context = await browser.newContext({
      viewport: { width: 1680, height: 1220 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();

    await captureFullScreenshots(page);
    await captureCardScreenshots(page);
    await context.close();

    await buildGif();
    console.log(`[capture] GIF 저장 완료: ${gifOutputPath}`);
  } finally {
    await browser.close();

    if (serverProcess) {
      serverProcess.kill();
    }
  }
}

await ensureOutputDirectory();
await captureScreenshots();
