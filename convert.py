#!/usr/bin/env python3
# Copyright Adam C. Knapp 2018-2020
# License: CC-BY 4.0 https://creativecommons.org/licenses/by/4.0/
#
# A cross-compiler, from a 3 state dynamical system written in MAX/MIN/NOT formulae to its canonical
# mod-3 polynomial representation or to a C-language simulator

import argparse
import os
import sys

from equation_system import *


####################################################################################################

def main():
    parser = argparse.ArgumentParser(
        description='Converter from MAX/MIN/NOT formulae to either \n' +
                    'low-degree polynomials over F_3 or a C-language simulator')
    parser.add_argument('-i', '--inputfile',
                        help='input filename containing MAX/MIN/NOT formulae. required. ',
                        type=str)
    parser.add_argument('-o', '--outputfile',
                        help='output filename for the polynomial formulae. if not provided, stdout is used',
                        type=str)
    parser.add_argument('-n', '--non_descriptive',
                        action='store_true',
                        help='use non-descriptive names for variables')
    parser.add_argument('-no-polys',
                        action='store_true',
                        help='do not output polynomials, used by default when output is by simulator')
    parser.add_argument('-sim',
                        action='store_true',
                        help='output C-language simulator program instead of formulae')
    parser.add_argument('--count',
                        type=int,
                        help='number of random points tried by the simulator, default 1,000,000.\n' +
                             'Ignored if the -sim flag is not used')
    parser.add_argument('-c', '--continuous',
                        action='store_true',
                        help='generate polynomials for continuous system, applied before the self-power operation')
    parser.add_argument('-comit', '--continuous-omit', nargs='+',
                        help='list of variables to _not_ apply continuity operation to')
    parser.add_argument('-power', '--self-power', type=int,
                        help='gets polynomials for a power of the system. i.e. self-composition, power-1 times ('
                             'default: 1) ignored for simulator Warning: This can take a long time!')
    parser.add_argument('-graph',
                        action='store_true',
                        help='use the graph-creation simulator')

    args = parser.parse_args()

    in_formulae: str = ''
    if args.inputfile:
        # read input formulae from file, first checking that the file is there, etc.
        try:
            if not os.path.isfile(args.inputfile):
                print('Input file does not exist')
            elif not os.access(args.inputfile, os.R_OK):
                print('Input file is not readable')
            with open(args.inputfile, 'r') as in_file:
                in_formulae = ''.join(in_file.readlines())
        except IOError:
            print('Error reading file')
    else:
        print("Input file required\n")
        parser.print_help()

    translate_symbol_names_to_xs = args.non_descriptive

    # parse the file into a system of equations
    equation_system = EquationSystem()
    for line in in_formulae.splitlines():
        equation_system.parse_and_add_equation(line)

    if not equation_system.consistent():
        print("Inconsistent system of equations!")
        sys.exit(-1)

    # impose continuity, if desired
    # TODO: non-polynomial continuity for the simulator?
    if not args.continuous and args.continuous_omit:
        print("Asked to omit continuity for system which was not continuous. This is probably an error; exiting.")
        sys.exit(-1)
    if args.continuous is not None and args.continuous:
        all_variables = [str(var) for var in equation_system.symbol_table()]
        if args.continuous_omit is not None:
            continuous_variables = [var for var in all_variables if var not in args.continuous_omit]
        else:
            continuous_variables = all_variables
        equation_system = equation_system.continuous_system(continuous_vars=continuous_variables)

    if args.sim:
        #################################################################
        # run the simulator
        #################################################################
        count = 1_000_000
        if args.count is not None:
            count = args.count

        if not args.no_polys:
            equation_system = equation_system.as_poly_system()

        use_graph = args.graph is not None and args.graph

        output_as_program(equation_system=equation_system,
                          output_file=args.outputfile,
                          num_runs=count,
                          graph=use_graph)
    else:
        #################################################################
        # output polynomials
        #################################################################

        # do composition if requested
        if args.self_power is not None:
            if args.self_power <= 0:
                raise Exception("Can only take positive power composition of system!")
            else:
                # Tried to see if this would help, no difference for the system I tried
                # equation_system = equation_system.as_poly_system()
                equation_system = equation_system.self_compose(count=args.self_power)

        output_as_formulae(equation_system=equation_system,
                           translate_symbol_names_to_xs=translate_symbol_names_to_xs,
                           as_polynomials=not args.no_polys,
                           output_file=args.outputfile)


####################################################################################################

def output_as_formulae(equation_system, translate_symbol_names_to_xs=True, as_polynomials=True, output_file=None):
    if as_polynomials:
        out_str_formulae = equation_system.polynomial_output(translate_symbol_names=translate_symbol_names_to_xs)
    else:
        out_str_formulae = str(equation_system)

    if output_file:
        try:
            with open(output_file, 'w') as file_out:
                file_out.write(out_str_formulae)
                file_out.write('\n')
        except IOError:
            print('Error writing to file')
    else:
        print(out_str_formulae)
        print()


####################################################################################################

def output_as_program(equation_system, output_file=None, num_runs=1_000_000, graph=False):
    # get symbols, in order
    symbols = tuple(equation_system.target_variables())

    # parameter lists
    typed_param_list = "int " + ", int ".join(symbols)
    param_list = ", ".join(symbols)

    # create the functions which compute the transitions
    output_functions = []
    function_names = []

    for target, formula in equation_system:
        if isinstance(formula, int):
            string_c_formula = str(formula)
        else:
            string_c_formula = formula.as_c_expression()

        function_name = target + "_next"
        function_names.append(function_name)

        function_template = """
int {function_name}({typed_param_list})
{{
  return ( {c_formula} ) % 3;
}}"""
        function = function_template.format(target=target,
                                            function_name=function_name,
                                            typed_param_list=typed_param_list,
                                            c_formula=string_c_formula)

        output_functions.append(function)

    # random state initializer
    variable_initialization = "\n".join(
        ["    int {name}_temp, {name} = {value} % 3;".format(name=symbol, value=int(formula))
         if isinstance(formula, int) or formula.is_constant() else
         "    int {name}_temp, {name} = rand() % 3;".format(name=symbol)
         for symbol, formula in equation_system])

    # run update, saving to temp variables
    def update_to_temp(indent=6):
        update_template = (indent * " ") + "{symbol}_temp = {func}({params});"
        return "\n".join([update_template.format(symbol=symbol,
                                                 func=fn_name,
                                                 params=param_list)
                          for symbol, fn_name in zip(symbols, function_names)])

    # copy the temp variables over
    def copy_temp_vars(indent=6):
        copy_template = (indent * " ") + "{symbol} = {symbol}_temp;"
        return "\n".join([copy_template.format(symbol=symbol)
                          for symbol in symbols])

    # stash current "initial" state for a cycle
    def state_stash(indent=4):
        stash_template = (indent * " ") + "int {symbol}_init = {symbol};"
        return "\n".join([stash_template.format(symbol=symbol) for symbol in symbols])

    # check against "initial" state
    # noinspection PyUnusedLocal
    def neq_check(indent=4):
        neq_check_template = "{symbol} == {symbol}_init"
        return "!(" + " && ".join([neq_check_template.format(symbol=symbol) for symbol in symbols]) + ")"

    # print it out
    def print_state(indent=12):
        print_template = (indent * " ") + 'printf("\\"{symbol}\\":%u, ", {symbol});'
        print_template_last = (indent * " ") + 'printf("\\"{symbol}\\":%u ", {symbol});'
        header = (indent * " ") + 'printf("{ ");\n'
        footer = (indent * " ") + 'printf("}");\n'
        return \
            header + \
            "\n".join([print_template.format(symbol=symbol) if num != len(symbols) - 1 else print_template_last.format(
                symbol=symbol)
                       for num, symbol in enumerate(symbols)]) + '\n' + \
            footer

    # hash functions
    hash_one = "\n".join(
        "  accumulator = 3*accumulator + {symbol};".format(symbol=symbol)
        for symbol in symbols)
    hash_two = "\n".join(
        "  accumulator = 5*accumulator + {symbol};".format(symbol=symbol)
        for symbol in symbols)
    hash_three = "\n".join(
        "  accumulator = 7*accumulator + {symbol};".format(symbol=symbol)
        for symbol in symbols)

    if graph:
        # alternate for graph!
        with open('gv-model-template.c', 'r') as file:
            template = file.read()
    else:
        # load the "big" template
        with open('model-template.c', 'r') as file:
            template = file.read()

    # and use it
    program_text = template.format(param_list=param_list,
                                   typed_param_list=typed_param_list,
                                   accumulate_hash_one=hash_one,
                                   accumulate_hash_two=hash_two,
                                   accumulate_hash_three=hash_three,
                                   update_functions="\n".join(output_functions),
                                   compute_next6=update_to_temp(indent=6),
                                   compute_next8=update_to_temp(indent=8),
                                   compute_next12=update_to_temp(indent=12),
                                   copy6=copy_temp_vars(indent=6),
                                   copy8=copy_temp_vars(indent=8),
                                   copy12=copy_temp_vars(indent=12),
                                   initialize_variables=variable_initialization,
                                   print_state=print_state(),
                                   variable_stash=state_stash(indent=4),
                                   neq_check=neq_check(indent=4),
                                   num_runs=num_runs)

    if output_file:
        try:
            with open(output_file, 'w') as out_file:
                out_file.write(program_text)
        except IOError:
            print('Error writing to file')
    else:
        print(program_text)


if __name__ == '__main__':
    # execute only if run as a script
    main()
