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
  tagline: "The Token-Efficient, Contract-Driven Coding Agent",
  description:
    "An autonomous CLI engineering teammate built around LangGraph, DeepAgents, and deterministic code-enforced gates. Runs in isolated git worktrees with strict budget controls and honest verification.",
  author: {
    name: "Andrew Hochoy",
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
      label: "Routing Speedup",
      value: "12×",
      sublabel: "Lower p95 latency vs generative LLMs using TypeSafe Jev",
    },
    {
      label: "Cost Reduction",
      value: "4× – 12×",
      sublabel: "Saved per plan via 2-tier models and prompt caching",
    },
    {
      label: "Unsafe Routing Errors",
      value: "0",
      sublabel: "Zero misrouted tasks on 50 live benchmark test cases",
    },
    {
      label: "Containment",
      value: "100%",
      sublabel: "Worktree isolation, sanitized subshell envs & null keyrings",
    },
  ],
};
