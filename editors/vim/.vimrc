"---------------------------------vim config----------------------------
syntax on
set background=dark
set number
set mouse=a
set clipboard=unnamed
set showcmd
set ruler
set encoding=utf-8
set showmatch
set sw=2
set relativenumber
set laststatus=2
set backspace=2
set guioptions-=T
set guioptions-=L
"Get out of insert mode 
"Salir de modo insertar
imap ii <Esc>
"imap <C-c> <Esc>l
"mueve bloques de codigo en modo visual o V-Line
"Moves Blocks of code in visual mode or V-Line
xnoremap K :move '<-2<CR>gv-gv
xnoremap J :move '>+1<CR>gv-gv 

" Better indenting
" Mejor Indentación
vnoremap < <gv
vnoremap > >gv

" Automatically save and load folds
autocmd BufWinLeave *.* mkview
autocmd BufWinEnter *.* silent loadview"

"-------------------------------Plugins-------------------------------
if empty(glob('~/.vim/autoload/plug.vim'))
      silent !curl -fLo ~/.vim/autoload/plug.vim --create-dirs
            \ https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif
"Plugins
call plug#begin('~/.vim/plugged')

" Themes
Plug 'morhetz/gruvbox'
Plug 'shinchu/lightline-gruvbox.vim'
Plug 'sheerun/vim-polyglot'

" "IDE
Plug 'easymotion/vim-easymotion'

" Languages
Plug 'pangloss/vim-javascript'    " JavaScript support
Plug 'leafgarland/typescript-vim' " TypeScript syntax

"Nerdtree
Plug 'preservim/nerdtree'

"Navigate with C-h C-l C-j C-k
"Navegar con C-h C-l C-j C-k
Plug 'christoomey/vim-tmux-navigator'

"Airline
Plug 'vim-airline/vim-airline'
Plug 'vim-airline/vim-airline-themes'

" Stable version of coc
Plug 'neoclide/coc.nvim', {'branch': 'release'}

"Close pairs () [] {} ''
"Cerrar los pares () [] {} '' 
Plug 'jiangmiao/auto-pairs'
Plug 'alvan/vim-closetag'

"FZF
Plug 'junegunn/fzf', { 'do': { -> fzf#install() } }
Plug 'junegunn/fzf.vim',

call plug#end()

source $DOTFILES_PATH/editors/vim/plugins/plug-config.vim
"--------------------------------Plugins Config--------------------------------------------

let g:coc_global_extensions = [ 'coc-tsserver' ]

let g:airline_powerline_fonts = 1
" GoTo code navigation.
nmap <silent> gd <Plug>(coc-definition)
nmap <silent> gy <Plug>(coc-type-definition)
nmap <silent> gi <Plug>(coc-implementation)
nmap <silent> gr <Plug>(coc-references)


let mapleader = " "
noremap <leader>w :w<cr>
noremap <leader>gs :CocSearch
noremap <leader>fs :Files<cr>
noremap <leader><cr> <cr><c-w>h:q<cr>

"save file
"guardar archivo
nmap <leader>w :w <CR>
"cerrar ventana
"close current  window
nmap <C-w> :q <CR>
nmap <leader>q :q <CR>
nmap <leader>so :so%<CR>
"search commands 
"comandos de busqueda
nmap <leader>gs  :CocSearch
nmap <leader>fs :FZF<CR>
nmap <leader>rg :Rg<CR>


"abrir Nerdtree
"open nerdtree
nmap <Leader>nt :NERDTreeFind<CR>
"Buscar dos carácteres con easymotion
"Search for two chars with easymotion
nmap <Leader>s <Plug>(easymotion-s2)

" TAB in general mode will move to text buffer
" TAB en modo normal se moverá al siguiente buffer
nnoremap <silent> <TAB> :bnext<CR>
" SHIFT-TAB will go back
" SHIFT-TAB va para atras 
nnoremap <silent> <S-TAB> :bprevious<CR>
"close buffer
"cerrar buffer
nmap <leader>bd :bdelete<CR>



"LightLine Config
" let g:lightline.colorscheme = 'gruvbox'
let g:lightline = {
      \ 'active': {
      \   'left': [['mode', 'paste'], [], ['relativepath', 'modified']],
      \   'right': [['kitestatus'], ['filetype', 'percent', 'lineinfo'], ['gitbranch']]
      \ },
      \ 'inactive': {
      \   'left': [['inactive'], ['relativepath']],
      \   'right': [['bufnum']]
      \ },
      \ 'component': {
      \   'bufnum': '%n',
      \   'inactive': 'inactive'
      \ },
      \ 'component_function': {
      \   'gitbranch': 'fugitive#head',
      \   'kitestatus': 'kite#statusline'
      \ },
      \ 'colorscheme': 'gruvbox',
      \ 'subseparator': {
      \   'left': '',
      \   'right': ''
      \ }
      \}

"--gruvbox config--
colorscheme gruvbox
let g:gruvbox_contrast_dark = "medium"
highlight Normal ctermbg=234
set laststatus=2
set noshowmode

"Close tags automatically
"Cerrar tags automaticamente
let g:closetag_filenames = '*.html,*.xhtml,*.phtml, *.jsx, *.js'
"-----------------------------------------------------------------


" Set compatibility to Vim only.
set nocompatible
set nolist
set rnu
" Helps force plug-ins to load correctly when it is turned back on below.
filetype off

" Turn on syntax highlighting.
syntax on

" For plug-ins to load correctly.
filetype plugin indent on

" Turn off modelines
set modelines=0

" Automatically wrap text that extends beyond the screen length.
set wrap
" Vim's auto indentation feature does not work properly with text copied from outside of Vim. Press the <F2> key to toggle paste mode on/off.
nnoremap <F2> :set invpaste paste?<CR>
imap <F2> <C-O>:set invpaste paste?<CR>
set pastetoggle=<F2>

" Uncomment below to set the max textwidth. Use a value corresponding to the width of your screen.
set textwidth=79
set formatoptions=tcqrn1
set tabstop=2
set shiftwidth=2
set softtabstop=2
set expandtab
set noshiftround

" Display 5 lines above/below the cursor when scrolling with a mouse.
set scrolloff=5
" Fixes common backspace problems
set backspace=indent,eol,start

" Speed up scrolling in Vim
set ttyfast

" Display options
" set showmode
" set showcmd

" Highlight matching pairs of brackets. Use the '%' character to jump between them.
set matchpairs+=<:>

" Set status line display
set statusline=%F%m%r%h%w\ [FORMAT=%{&ff}]\ [TYPE=%Y]\ [POS=%l,%v][%p%%]\ [BUFFER=%n]\ %{strftime('%c')}

" Highlight matching search patterns
set hlsearch
" Enable incremental search
set incsearch
" Include matching uppercase words with lowercase search term
set ignorecase
" Include only uppercase words with uppercase search term
set smartcase

" Store info from no more than 100 files at a time, 9999 lines of text, 100kb of data. Useful for copying large amounts of data between files.
set viminfo='100,<9999,s100

" Map the <Space> key to toggle a selected fold opened/closed.
nnoremap <silent> <Space> @=(foldlevel('.')?'za':"\<Space>")<CR>
vnoremap <Space> zf