highlight clear

let g:colors_name='dark-redux'

function s:highlight(group, bg, fg, style)
  let gui = a:style == '' ? '' : 'gui=' . a:style
  let fg = a:fg == '' ? '' : 'guifg=' . a:fg
  let bg = a:bg == '' ? '' : 'guibg=' . a:bg
  exec 'hi ' . a:group . ' ' . bg . ' ' . fg  . ' ' . gui
endfunction

let s:Color10 = '#5f5f5f'
let s:Color7 = '#4EC9B0'
let s:Color2 = '#f44747'
let s:Color5 = '#d4d4d4'
let s:Color9 = '#9CDCFE'
let s:Color3 = '#569cd6'
let s:Color12 = '#212121'
let s:Color1 = '#b5cea8'
let s:Color13 = '#1e1e1e'
let s:Color0 = '#6A9955'
let s:Color11 = '#6e6e6e'
let s:Color6 = '#DCDCAA'
let s:Color14 = '#3c3c3c'
let s:Color4 = '#ce9178'
let s:Color8 = '#C586C0'

call s:highlight('Comment', '', s:Color0, '')
call s:highlight('Number', '', s:Color1, '')
call s:highlight('Error', '', s:Color2, '')
call s:highlight('Type', '', s:Color3, '')
call s:highlight('String', '', s:Color4, '')
call s:highlight('Keyword', '', s:Color3, '')
call s:highlight('Conditional', '', s:Color3, '')
call s:highlight('Repeat', '', s:Color3, '')
call s:highlight('Operator', '', s:Color5, '')
call s:highlight('Function', '', s:Color6, '')
call s:highlight('Type', '', s:Color7, '')
call s:highlight('Conditional', '', s:Color8, '')
call s:highlight('Repeat', '', s:Color8, '')
call s:highlight('Identifier', '', s:Color9, '')
call s:highlight('TSCharacter', '', s:Color3, '')
call s:highlight('Comment', '', s:Color10, '')
call s:highlight('Conditional', '', '', 'italic')
call s:highlight('Repeat', '', '', 'italic')
call s:highlight('StatusLine', s:Color11, s:Color12, '')
call s:highlight('WildMenu', s:Color13, s:Color5, '')
call s:highlight('Pmenu', s:Color13, s:Color5, '')
call s:highlight('PmenuSel', s:Color5, '', '')
call s:highlight('PmenuThumb', s:Color13, s:Color5, '')
call s:highlight('Normal', s:Color13, s:Color5, '')
call s:highlight('SignColumn', s:Color13, '', '')
call s:highlight('LineNr', '', s:Color14, '')
call s:highlight('TabLine', s:Color12, '', '')
call s:highlight('TabLineFill', s:Color12, '', '')
call s:highlight('TSPunctDelimiter', '', s:Color5, '')

highlight! link TSField Constant
highlight! link TSPunctSpecial TSPunctDelimiter
highlight! link TSTag MyTag
highlight! link TSFloat Number
highlight! link TSFunction Function
highlight! link TSParameter Constant
highlight! link TSOperator Operator
highlight! link TSNamespace TSType
highlight! link Operator Keyword
highlight! link CursorLineNr Identifier
highlight! link TSKeyword Keyword
highlight! link NonText Comment
highlight! link Conditional Operator
highlight! link TSTagDelimiter Type
highlight! link TSConstant Constant
highlight! link TSConstBuiltin TSVariableBuiltin
highlight! link Whitespace Comment
highlight! link TSString String
highlight! link TelescopeNormal Normal
highlight! link TSPunctBracket MyTag
highlight! link Repeat Conditional
highlight! link TSRepeat Repeat
highlight! link TSProperty TSField
highlight! link TSConditional Conditional
highlight! link TSType Type
highlight! link Folded Comment
highlight! link TSLabel Type
highlight! link TSParameterReference TSParameter
highlight! link TSFuncMacro Macro
highlight! link TSComment Comment
highlight! link TSNumber Number
highlight! link Macro Function
