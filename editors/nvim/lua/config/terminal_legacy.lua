local function terminal_command(command)
  if command == nil or command == "" then
    return vim.o.shell
  end

  return command
end

local function open_terminal(opts)
  opts = opts or {}

  local command = terminal_command(opts.command)
  local direction = opts.direction or "horizontal"

  if direction == "vertical" then
    vim.cmd("botright 80vsplit")
  elseif direction == "tab" then
    vim.cmd("tabnew")
  else
    vim.cmd("botright 12split")
  end

  vim.cmd("enew")
  vim.bo.bufhidden = "wipe"
  vim.bo.swapfile = false
  vim.bo.filetype = "terminal"

  vim.fn.termopen(command, {
    on_exit = function(_, code)
      if code ~= 0 then
        vim.schedule(function()
          vim.notify("Terminal command exited with code " .. code, vim.log.levels.INFO, { title = "Terminal" })
        end)
      end
    end,
  })
  vim.cmd("startinsert")
end

vim.api.nvim_create_user_command("Terminal", function(command)
  open_terminal({ command = command.args, direction = "horizontal" })
end, {
  desc = "Open a native terminal split for short editor-local commands",
  nargs = "*",
  complete = "shellcmd",
})

vim.api.nvim_create_user_command("TerminalVertical", function(command)
  open_terminal({ command = command.args, direction = "vertical" })
end, {
  desc = "Open a native vertical terminal split for short editor-local commands",
  nargs = "*",
  complete = "shellcmd",
})

vim.api.nvim_create_user_command("TerminalTab", function(command)
  open_terminal({ command = command.args, direction = "tab" })
end, {
  desc = "Open a native terminal tab for short editor-local commands",
  nargs = "*",
  complete = "shellcmd",
})

vim.keymap.set("n", "<leader>tt", "<cmd>Terminal<CR>", { desc = "Open terminal split" })
vim.keymap.set("n", "<leader>tT", "<cmd>TerminalVertical<CR>", { desc = "Open vertical terminal" })
vim.keymap.set("n", "<leader>tc", function()
  vim.ui.input({ prompt = "Terminal command: " }, function(input)
    if input and input ~= "" then
      open_terminal({ command = input, direction = "horizontal" })
    end
  end)
end, { desc = "Run terminal command" })
vim.keymap.set("t", "<Esc><Esc>", [[<C-\><C-n>]], { desc = "Leave terminal mode" })
