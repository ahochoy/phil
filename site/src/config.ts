export interface SiteConfig {
  title: string;
  /** What Phil is, said under its name in the hero, the nav and the page title. */
  subtitle: string;
  description: string;
  author: {
    name: string;
    url: string;
    github: string;
    linkedin: string;
  };
  links: {
    github: string;
    guide: string;
  };
}

export const SITE_CONFIG: SiteConfig = {
  title: "Phil",
  subtitle: "a coding agent experiment",
  description:
    "Phil is a coding agent experiment for the terminal, built on LangGraph: each request goes down the workflow that fits it, and the cheaper model does the work wherever that's enough.",
  author: {
    name: "Andrew HoChoy",
    url: "https://andrewhochoy.com/",
    github: "https://github.com/ahochoy",
    linkedin: "https://www.linkedin.com/in/andrewhochoy/",
  },
  links: {
    github: "https://github.com/ahochoy/phil",
    guide: "https://github.com/ahochoy/phil/blob/main/docs/guide.md",
  },
};
