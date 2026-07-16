local function git_root()
  if vim.fn.executable("git") ~= 1 then
    return nil
  end

  local file = vim.api.nvim_buf_get_name(0)
  local path = file ~= "" and vim.fn.fnamemodify(file, ":p:h") or vim.loop.cwd()
  local result = vim.fn.systemlist({ "git", "-C", path, "rev-parse", "--show-toplevel" })

  if vim.v.shell_error ~= 0 or not result[1] or result[1] == "" then
    return nil
  end

  return result[1]
end

local function has_git_root()
  return git_root() ~= nil
end

local function diffview_open(args)
  if not has_git_root() then
    vim.notify("Diffview is available only inside a git repository", vim.log.levels.INFO, { title = "Git" })
    return
  end

  vim.cmd("DiffviewOpen" .. (args and args ~= "" and (" " .. args) or ""))
end

local function diffview_file_history(args)
  if not has_git_root() then
    vim.notify("Diffview file history is available only inside a git repository", vim.log.levels.INFO, { title = "Git" })
    return
  end

  vim.cmd("DiffviewFileHistory" .. (args and args ~= "" and (" " .. args) or ""))
end

return {
  {
    "lewis6991/gitsigns.nvim",
    cmd = "Gitsigns",
    event = {
      "BufReadPre",
      "BufNewFile",
    },
    opts = {
      signs = {
        add = { text = "│" },
        change = { text = "│" },
        delete = { text = "_" },
        topdelete = { text = "‾" },
        changedelete = { text = "~" },
        untracked = { text = "┆" },
      },
      signcolumn = true,
      numhl = false,
      linehl = false,
      word_diff = false,
      current_line_blame = false,
      watch_gitdir = {
        follow_files = true,
      },
      attach_to_untracked = true,
      update_debounce = 200,
      preview_config = {
        border = "rounded",
      },
      on_attach = function(bufnr)
        local gitsigns = require("gitsigns")

        local function map(mode, lhs, rhs, desc, opts)
          opts = vim.tbl_extend("force", { buffer = bufnr, desc = desc }, opts or {})
          vim.keymap.set(mode, lhs, rhs, opts)
        end

        map("n", "]h", function()
          if vim.wo.diff then
            return "]h"
          end
          vim.schedule(gitsigns.next_hunk)
          return "<Ignore>"
        end, "Next git hunk", { expr = true })

        map("n", "[h", function()
          if vim.wo.diff then
            return "[h"
          end
          vim.schedule(gitsigns.prev_hunk)
          return "<Ignore>"
        end, "Previous git hunk", { expr = true })
        map("n", "<leader>gp", gitsigns.preview_hunk, "Preview git hunk")
        map("n", "<leader>gr", gitsigns.reset_hunk, "Reset git hunk")
        map("n", "<leader>gs", gitsigns.stage_hunk, "Stage git hunk")
        map("n", "<leader>gu", gitsigns.undo_stage_hunk, "Undo stage hunk")
        map("n", "<leader>gb", gitsigns.blame_line, "Show git blame for line")
        map("n", "<leader>gB", gitsigns.toggle_current_line_blame, "Toggle current line blame")
        map("n", "<leader>gD", gitsigns.diffthis, "Diff current file")
        map("n", "<leader>gQ", gitsigns.setqflist, "Git hunks to quickfix")
        map("n", "<leader>gt", gitsigns.toggle_deleted, "Toggle deleted lines")
        map("v", "<leader>gr", function()
          gitsigns.reset_hunk({ vim.fn.line("."), vim.fn.line("v") })
        end, "Reset selected git hunk")
        map("v", "<leader>gs", function()
          gitsigns.stage_hunk({ vim.fn.line("."), vim.fn.line("v") })
        end, "Stage selected git hunk")
      end,
    },
  },
  {
    "sindrets/diffview.nvim",
    cmd = {
      "DiffviewClose",
      "DiffviewFileHistory",
      "DiffviewFocusFiles",
      "DiffviewLog",
      "DiffviewOpen",
      "DiffviewRefresh",
      "DiffviewToggleFiles",
    },
    keys = {
      {
        "<leader>gd",
        function()
          diffview_open()
        end,
        desc = "Open git diff review",
      },
      {
        "<leader>gH",
        function()
          diffview_file_history("%")
        end,
        desc = "Git file history review",
      },
      { "<leader>gq", "<cmd>DiffviewClose<CR>", desc = "Close git diff review" },
    },
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-tree/nvim-web-devicons",
    },
    init = function()
      vim.api.nvim_create_user_command("GitDiffReview", function(command)
        diffview_open(command.args)
      end, {
        desc = "Open the lazy git diff review workflow",
        nargs = "*",
      })

      vim.api.nvim_create_user_command("GitFileHistory", function(command)
        diffview_file_history(command.args ~= "" and command.args or "%")
      end, {
        desc = "Open the lazy git file history workflow",
        nargs = "*",
      })
    end,
    opts = {
      enhanced_diff_hl = true,
      use_icons = true,
      view = {
        merge_tool = {
          layout = "diff3_mixed",
        },
      },
      file_panel = {
        listing_style = "tree",
      },
    },
  },
}
