export interface SiteConfig {
  title: string;
  tagline: string;
  description: string;
  author: {
    name: string;
    role: string;
    url: string;
    github: string;
    devlog: string;
    linkedin?: string;
  };
  links: {
    github: string;
    githubIssues: string;
    documentation: string;
    personalSite: string;
    devlog: string;
    devlogPart1: string;
    cal: string;
  };
  metrics: {
    label: string;
    value: string;
    sublabel: string;
  }[];
}

export const SITE_CONFIG: SiteConfig = {
  title: "Phil",
  tagline: "A coding agent that knows what it costs",
  description:
    "Phil is an open-source coding agent for the terminal. It plans with you, works in its own git worktree, checks its work with real commands, and stops before it spends more than you allowed.",
  author: {
    name: "Andrew HoChoy",
    role: "AI Systems & Software Engineer",
    url: "https://andrewhochoy.com/",
    github: "https://github.com/ahochoy",
    devlog: "https://andrewhochoy.com/devlog/",
    linkedin: "https://www.linkedin.com/in/andrewhochoy/",
  },
  links: {
    github: "https://github.com/ahochoy/phil",
    githubIssues: "https://github.com/ahochoy/phil/issues",
    documentation: "https://github.com/ahochoy/phil#readme",
    personalSite: "https://andrewhochoy.com/",
    devlog: "https://andrewhochoy.com/devlog/",
    devlogPart1: "https://andrewhochoy.com/devlog/2026-10-08-building-phil-1-stop-the-waste",
    cal: "https://cal.com/andrew-hochoy/intro-call",
  },
  metrics: [
    {
      label: "Faster routing",
      value: "12×",
      sublabel: "p95 latency, Jev vs. an LLM router",
    },
    {
      label: "Cheaper plans",
      value: "4 to 12×",
      sublabel: "From two model tiers and prompt caching",
    },
    {
      label: "Unsafe routes",
      value: "0",
      sublabel: "Across 50 live benchmark runs",
    },
    {
      label: "API keys in agent shells",
      value: "0",
      sublabel: "Env stripped, keyring backend set to null",
    },
  ],
};
